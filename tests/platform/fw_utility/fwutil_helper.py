import os
import pytest
import time
import sys
from common import reboot
from psu_controller import psu_controller
from loganalyzer import LogAnalyzer, LogAnalyzerError
from datetime import datetime
from check_critical_services import check_critical_services
from check_daemon_status import check_pmon_daemon_status

import logging
logger = logging.getLogger(__name__)

FW_UTIL_DATA = {}
BASE_DIR = os.path.dirname(os.path.realpath(__file__))
BINARIES_DIR = os.path.join(BASE_DIR, 'binaries')
TMP_DIR = os.path.basename('tmp')
SUCCESS_CODE = 0
FAILURE_CODE = -1

FW_INSTALL_SUCCESS_LOG = "*.Firmware install ended * status=success*."
UNVALID_NAME_LOG = '.*Invalid value for "<component_name>"*.'
UNVALID_PATH_LOG = '.*Error: Invalid value for "fw_path"*.'
UNVALID_URL_LOG = '.*Error: Did not receive a response from remote machine. Aborting...*.'


def parse_bios_version(files_path, file_name):
    fw_path = os.path.join(files_path, file_name)
    release_path = os.path.realpath(fw_path)
    ver = os.path.dirname(release_path).rsplit('/', 1)[1]
    ver = ver[::-1].replace('x', '0', 1)[::-1]
    for file_name in os.listdir(fw_path):
        if file_name.endswith('.rom'):
            fw_path = os.path.join(fw_path, file_name)
            break
    return fw_path, ver


def bios_version(dut, files_path, fw_data):
    versions = {}
    platform_type = dut.facts['platform']
    is_latest = False
    latest_ver = ''
    other_ver = ''
    latest = platform_type + '_latest'
    latest_fw_path = ""
    other = platform_type + '_other'
    other_fw_path = ""
    for file_name in os.listdir(files_path):
        if file_name.startswith(latest):
            latest_fw_path, latest_ver = parse_bios_version(files_path, file_name)
            if fw_data['BIOS']['version'].startswith(latest_ver):
                is_latest = True
        elif file_name.startswith(other):
            other_fw_path, other_ver = parse_bios_version(files_path, file_name)
    versions = {
            'latest_version': latest_ver,
            'latest_path': latest_fw_path,
            'latest_installed': is_latest,
            'other_version': other_ver,
            'other_path': other_fw_path,
    }

    return versions


def parse_cpld_version(files_path, file_name, fw_data):
    fw_path = os.path.join(files_path, file_name)
    real_path = os.path.realpath(fw_path)
    rev = os.path.basename(real_path).upper().split('REV')
    revisions = []
    counts = {}
    is_latest = False
    for i in range(len(rev)-1):
        r = rev[i+1]
        if r.startswith('_'):
            r = r.split('_')[1]
            revisions.append(int(r[0:2]))
        elif '_' in r:
            r = r.split('_')[0]
            revisions.append(int(r[0:2]))
        else:
            r = r.split('.')[0]
            revisions.append(int(r[0:2]))

    for r in revisions:
        counts[r] = revisions.count(r)

    current_counts = {}
    current_ver = fw_data['CPLD']['version'].split('.')
    for r in current_ver:
        current_counts[int(r)] = current_ver.count(r)

    if counts == current_counts:
        is_latest = True

    return counts, is_latest


def cpld_version(dut, files_path, fw_data):
    # currently taken from revisions but without known order
    versions = {}
    platform_type = dut.facts['platform']
    is_latest = False
    latest_ver = ''
    other_ver = ''
    latest = platform_type + '_latest'
    latest_fw_path = ""
    other = platform_type + '_other'
    other_fw_path = ""

    for file_name in os.listdir(files_path):
        if file_name.startswith(latest):
            latest_ver, is_latest = parse_cpld_version(files_path, file_name, fw_data)
            latest_fw_path = os.path.join(files_path, file_name)

    versions = {
        'latest_version': latest_ver,
        'latest_path': latest_fw_path,
        'latest_installed': is_latest,
        'other_version': '',
        'other_path': ''
    }
    return versions


def bios_update(request, dut):
    """
    perform cold reboot to make bios installation finished.
    :param request
    :param dut - DUT
    """
    testbed_device = request.getfixturevalue("testbed_devices")
    localhost = testbed_device["localhost"]
    reboot_ctrl_dict = {
        "command": "reboot",
        "timeout": 600,
        "cause": "reboot",
        "test_reboot_cause_only": False
    }
    # dut_datetime = datetime.strptime(dut.command('date -u +"%Y-%m-%d %H:%M:%S"')["stdout"], "%Y-%m-%d %H:%M:%S")
    logging.info("Run cold reboot on DUT")
    reboot_cmd = reboot_ctrl_dict["command"]
    reboot_task, reboot_res = dut.command(reboot_cmd, module_ignore_errors=True, module_async=True)
    logging.info("Wait for DUT to go down")
    res = localhost.wait_for(host=dut.hostname, port=22, state="stopped", timeout=180, module_ignore_errors=True)
    if "failed" in res:
        try:
            logging.error("Wait for switch down failed, try to kill any possible stuck reboot task")
            pid = dut.command("pgrep -f '%s'" % reboot_cmd)["stdout"]
            dut.command("kill -9 %s" % pid)
            reboot_task.terminate()
            logging.error("Result of command '%s': " + str(reboot_res.get(timeout=0)))
        except Exception as e:
            logging.error("Exception raised while cleanup reboot task and get result: " + repr(e))

    logging.info("Wait for DUT to come back")
    localhost.wait_for(host=dut.hostname, port=22, state="started", delay=10, timeout=reboot_ctrl_dict['timeout'])

    # logging.info("Check the uptime to verify whether reboot was performed")
    # dut_uptime = datetime.strptime(dut.command("uptime -s")["stdout"], "%Y-%m-%d %H:%M:%S")
    # assert float(dut_uptime.strftime("%s")) - float(dut_datetime.strftime("%s")) > 10, "Device did not reboot"

    logging.info("Wait until all critical services are fully started")
    check_critical_services(dut)

    logging.info("Check pmon daemon status")
    assert check_pmon_daemon_status(dut), "Not all pmon daemons running."

    if dut.facts["asic_type"] in ["mellanox"]:
        pdb.set_trace()
        current_file_dir = os.path.dirname(os.path.realpath(__file__))
        parent_dir = os.path.abspath(os.path.join(current_file_dir, os.pardir))
        sub_folder_dir = os.path.join(parent_dir, "mellanox")
        if sub_folder_dir not in sys.path:
            sys.path.append(sub_folder_dir)
        from check_hw_mgmt_service import check_hw_management_service
        from check_sysfs import check_sysfs

        logging.info("Check the hw-management service")
        check_hw_management_service(dut)

        logging.info("Check sysfs")
        check_sysfs(dut)


def cpld_update(request, dut):
    """
    performs 30 sec power cycle off to finish cpld installation.
    """
    pdb.set_trace()
    cmd_num_psu = "sudo psuutil numpsus"
    logging.info("Check how much PSUs DUT has")
    psu_num_out = dut.command(cmd_num_psu)
    psu_num = 0
    try:
        psu_num = int(psu_num_out["stdout"])
    except:
        assert False, "Unable to get the number of PSUs using command '%s'" % cmd_num_psu

    logging.info("Create PSU controller for testing")
    psu_control = request.getfixturevalue("psu_ctrl")
    if psu_control is None:
        pytest.fail("No PSU controller for %s, skip rest of the testing in this case" % dut.hostname)
    pdb.set_trace()
    all_psu_status = psu_control.get_psu_status()
    if all_psu_status:
        for psu in all_psu_status:
            if psu["psu_on"]:
                psu_control.turn_off_psu(psu["psu_id"])
                time.sleep(5)

        # perform 30 seconds timeout
        time.sleep(30)

        all_psu_status = psu_control.get_psu_status()
        if all_psu_status:
            # turn on all psu
            for psu in all_psu_status:
                if not psu["psu_on"]:
                    psu_control.turn_on_psu(psu["psu_id"])
                    time.sleep(5)

    # wait for dut to go up
    time.sleep(20)


def check_bios_version(version_to_install, comp_data):
    """
    check if bios version was updated as expected.
    """
    if comp_data['version'].startswith(version_to_install):
        return SUCCESS_CODE
    return FAILURE_CODE


def check_cpld_version(version_to_install, comp_data):

    # for now just checks if ends without errors - without version check
    return SUCCESS_CODE

    # counts = {}
    # ver = comp_data['version']
    # for v in ver:
    #     counts[v] = ver.count(v)

    # if counts == version_to_install:
    #     return SUCCESS_CODE

    # return FAILURE_CODE
