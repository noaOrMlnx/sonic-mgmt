import os
import re
import pytest
import time
import sys
import json
import random
from common.utilities import wait_until
from fwutil_helper import *
from loganalyzer import LogAnalyzer, LogAnalyzerError

import logging
logger = logging.getLogger(__name__)


def fw_status(dut):
    """
    Gets fwutil show status command output
    :param dut: DUT
    """
    result = dut.command("fwutil show status")
    if result['rc'] != SUCCESS_CODE:
        pytest.fail("Could not execute command 'fwutil show status'")
        raise result['stderr']
    return result['stdout']


def get_output_data(dut):
    """
    Parse output of 'fwutil show status'
    and return the data
    :param dut: DUT
    """
    num_spaces = 2
    status_output = fw_status(dut)
    output_data = {}
    separators = re.split(r'\s{2,}', status_output.splitlines()[1])  # get separators
    output_lines = status_output.splitlines()[2:]
    for line in output_lines:
        data = []
        start = 0
        for sep in separators:
            curr_len = len(sep)
            data.append(line[start:start+curr_len].strip())
            start += curr_len + num_spaces

        component = data[2]
        output_data[component] = {
            'chassis': data[0],
            'module': data[1],
            'version': data[3],
            'desc': data[4]
        }
    return output_data


def get_versions(dut, binaries_location):
    """
    Get versions of all components.
    """
    fw_data = get_output_data(dut)
    platform_type = dut.facts['platform']
    comp_versions = {}

    for comp in fw_data:
        comp_path = os.path.join(binaries_location, comp.lower())
        comp_versions[comp] = globals()[comp.lower() + '_version'](dut, comp_path, fw_data)

    return comp_versions


def process_versions(dut, components_list, location):
    """
    process latest/other versions of arbitrary picked component
    """
    versions = get_versions(dut, location)
    # pick arbitrary component
    current_comp = random.choice(components_list)
    comp_versions = versions[current_comp]

    if comp_versions['latest_installed']:
        is_latest = True
        current = comp_versions['latest_path']
        fw_path = comp_versions['other_path']
        version_to_install = comp_versions['other_version']
        previous_ver = comp_versions['latest_version']

    else:
        is_latest = False
        current = comp_versions['other_path']
        fw_path = comp_versions['latest_path']
        version_to_install = comp_versions['latest_version']
        previous_ver = comp_versions['other_version']

    return {
        'is_latest_installed': is_latest,
        'current_component': current_comp,
        'current_fw_path': current,
        'path_to_install': fw_path,
        'version_to_install': version_to_install,
        'previous_ver': previous_ver
    }


@pytest.fixture(scope='function')
def get_fw_path(request, testbed_devices, components_list):
    """
    fixture that returns fw paths.
    :param request: request for binaries path entered by the user.
    :param testbed_devices
    :param components_list: list of components
    """
    dut = testbed_devices["dut"]
    binaries_path = request.config.getoption("--binaries_path")
    if binaries_path is None:
        pytest.fail("Missing Arguments")
    yield process_versions(dut, components_list, binaries_path)


def execute_update_cmd(request, dut, cmd, component, version_to_install, expected_log):
    """
    execute the recievd command on DUT, perform the final update, and check validation.
    """
    loganalyzer = LogAnalyzer(ansible_host=dut, marker_prefix='acl')
    loganalyzer.load_common_config()
    try:
        loganalyzer.except_regex = [expected_log]
        with loganalyzer:
            result = dut.command(cmd)
    except LogAnalyzerError as err:
        raise err

    if result['rc'] != SUCCESS_CODE:
        raise result['stderr']

    # complete fw update - cold reboot if BIOS, power cycle with 30 sec timeout if CPLD
    globals()[component.lower() + '_update'](request, dut)
    # check output of show command
    fw_data = get_output_data(dut)
    comp_data = fw_data[component]
    if not comp_data['version']:
        pytest.fail("Installation didn't work. Aborting!")

    return globals()['check_' + component.lower() + '_version'](version_to_install, comp_data)


@pytest.fixture(scope='function')
def install_local(request, testbed_devices, testbed, get_fw_path):
    """
    performs fw installation from local path.
    """
    dut = testbed_devices['dut']
    # copy fw to dut and install
    comp_path = os.path.join("/tmp", get_fw_path['current_component'])
    dut.command("mkdir -p {}".format(comp_path))
    dut.copy(src=get_fw_path['path_to_install'], dest=comp_path)
    command = "fwutil install chassis component {} fw -y {}".format(get_fw_path['current_component'],
                                            os.path.join(comp_path, os.path.basename(get_fw_path['path_to_install'])))
    install_code = execute_update_cmd(request, dut, cmd=command, component=get_fw_path['current_component'],
                        version_to_install=get_fw_path['version_to_install'], expected_log=FW_INSTALL_SUCCESS_LOG)
    if install_code != SUCCESS_CODE:
        pytest.fail("Installation Failed. Aborting!")

    # recover previous fw only if previous was the latest:
    if get_fw_path['is_latest_installed'] is True:
        dut.command("rm -rf {}".format(comp_path))
        dut.command("mkdir -p {}".format(comp_path))
        dut.copy(src=get_fw_path['current_fw_path'], dest=comp_path)
        command = "fwutil install chassis component {} fw -y {}".format(get_fw_path['current_component'],
                                            os.path.join(comp_path, os.path.basename(get_fw_path['current_fw_path'])))
        install_code = execute_update_cmd(request, dut, cmd=command, component=get_fw_path['current_component'],
                            version_to_install=get_fw_path['previous_ver'], expected_log=FW_INSTALL_SUCCESS_LOG)
        if install_code != SUCCESS_CODE:
            pytest.fail("Installation Failed. Aborting!")

    yield
    dut.command("rm -rf {}".format(comp_path))


def execute_wrong_command(dut, cmd, expected_log):
    """
    execute wrong command and verify that error occures.
    """
    result = dut.command(cmd, module_ignore_errors=True)
    if result['rc'] == SUCCESS_CODE:
        pytest.fail("Expected error code!")

    if not result['stderr'].find(expected_log):
        if not result['stdout'].find(expected_log):
            pytest.fail("Expected logs didn't occure!")


def generate_components_file(dut, components_list, current_comp, path_to_install, version_to_install):
    """
    generate new platform_components.json file
    """
    fw_data = get_output_data(dut)
    platform_type = dut.facts['platform']
    json_data = {}
    json_data['chassis'] = {}
    json_data['chassis'][platform_type] = {}
    json_data['chassis'][platform_type]['component'] = {}

    for comp in components_list:
        json_data['chassis'][platform_type]['component'][comp] = {}
        if current_comp == comp:
            json_data['chassis'][platform_type]['component'][comp]['firmware'] = path_to_install
            json_data['chassis'][platform_type]['component'][comp]['version'] = version_to_install
            json_data['chassis'][platform_type]['component'][comp]['info'] = fw_data[comp]['desc']

    with open(os.path.join(BASE_DIR, "tmp_platform_components.json"), "w") as comp_file:
        json.dump(json_data, comp_file)

    dst = "/usr/share/sonic/device/{}/platform_components.json".format(platform_type)
    dut.copy(src=os.path.join(BASE_DIR, "tmp_platform_components.json"), dest=dst)


def update(request, dut, cmd, current_comp, path_to_install, version_to_install, comp_path):
    """"
    Perform update command
    """
    dut.copy(src=path_to_install, dest=comp_path)
    update_code = execute_update_cmd(request, dut, cmd, current_comp, version_to_install, expected_log=FW_INSTALL_SUCCESS_LOG)
    if update_code != SUCCESS_CODE:
        pytest.fail("Update Failed. Aborting!")

    dut.command("rm -rf {}".format(comp_path))


def update_from_current_img(request, dut, get_fw_path, components_list):
    """
    update from current image test case
    """
    update_cmd = "fwutil update -y --image=current"
    current_component = get_fw_path['current_component']
    comp_path = os.path.join("/tmp", current_component)
    dut.command("mkdir -p {}".format(comp_path))
    comp_path = os.path.join(comp_path, os.path.basename(get_fw_path['path_to_install']))

    generate_components_file(dut, components_list, current_comp=current_component,
                                path_to_install=comp_path, version_to_install=get_fw_path['version_to_install'])

    update(request, dut, update_cmd, current_comp=current_component,
            path_to_install=get_fw_path['path_to_install'], version_to_install=get_fw_path['version_to_install'], comp_path=comp_path)

    if get_fw_path['is_latest_installed'] is True:
        dut.command("rm -rf {}".format(comp_path))
        comp_path = os.path.join("/tmp", current_component)
        dut.command("mkdir -p {}".format(comp_path))
        comp_path = os.path.join(comp_path, os.path.basename(get_fw_path['current_fw_path']))

        generate_components_file(dut, components_list, current_comp=current_component,
                                    path_to_install=comp_path, version_to_install=get_fw_path['previous_ver'])
        update(request, dut, update_cmd, current_comp=current_component,
                path_to_install=get_fw_path['current_fw_path'], version_to_install=get_fw_path['previous_ver'], comp_path=comp_path)

    dut.command("rm -rf {}".format(comp_path))


def get_image_info(dut):
    """
    @summary: Parse image info in output of command 'sonic_installer list'
    @param module: The AnsibleModule object
    @return: Return parsed image info in dict
    """
    cmd = "sudo sonic_installer list"
    result = dut.command(cmd)
    if result['rc'] != 0:
        pytest.fail('Failed to run %s, rc=%s, stdout=%s, stderr=%s' % (cmd, result['rc'], result['stdout'], result['stderr']))
    stdout = result['stdout']
    try:
        image_info = {}
        image_list_line = False
        for line in stdout.splitlines():
            if not image_list_line:
                if 'Current: ' in line:
                    image_info['current'] = line.split('Current: ')[1]
                if 'Next: ' in line:
                    image_info['next'] = line.split('Next: ')[1]
                if 'Available:' in line:
                    image_list_line = True
                    image_info['available'] = []
                    continue
            else:
                image_info['available'].append(line)
        return image_info
    except Exception as e:
        pytest.fail('Failed to parse image info from output of "%s", err=%s' % (cmd, str(e)))

    return None


def set_next_boot(request, dut):
    """
    Set other available image as next.
    If there is no other available image, get it from user arguments.
    """
    image_info = get_image_info(dut)
    next_img = image_info['next']
    if next_img == image_info['current']:
        for img in image_info['available']:
            if img != image_info['current']:
                next_img = img
                break
    if next_img == image_info['current']:
        try:
            second_image_path = request.config.getoption("--second_image_path")
            next_img = os.path.basename(second_image_path)
            dut.copy(src=second_image_path, dest='/home/admin')
            result = dut.command("sonic_installer install -y ./{}".format(next_img))
            if result['rc'] != SUCCESS_CODE:
                pytest.fail("Could not install image {}. Aborting!".format(next_img))
        except Exception as e:
            pytest.fail("Not enough images for this test. Aborting!")

    result = dut.command("sonic_installer set_next_boot {}".format(next_img))
    if result['rc'] != SUCCESS_CODE:
        pytest.fail("Could not set image {} as next boot. Aborting!".format(next_img))


def reboot_to_image(request, testbed_devices, image_type):
    """
    set the recieved image as default and reboot
    """
    dut = testbed_devices['dut']
    localhost = testbed_devices['localhost']
    # move to next image
    result = dut.command("sonic_installer set_default {}".format(image_type))
    if result['rc'] != SUCCESS_CODE:
        pytest.fail("Could not reboot the {} image".format(image_type))

    # reboot
    logging.info("Reboot the DUT to load image")
    reboot_task, reboot_res = dut.command("reboot", module_async=True)
    logging.info("Wait for DUT to go down")
    try:
        localhost.wait_for(host=dut.hostname, port=22, state="stopped", delay=10, timeout=300)
    except Exception as e:
        logging.error("DUT did not go down, exception: " + repr(e))
        if reboot_task.is_alive():
            logging.error("Rebooting is not completed")
            reboot_task.terminate()
            logging.error("reboot result %s" % str(reboot_res.get()))

    logging.info("Wait for DUT to come back")
    localhost.wait_for(host=dut.hostname, port=22, state="started", delay=10, timeout=300)

    logging.info("Wait until system is stable")
    wait_until(300, 30, dut.critical_services_fully_started)

    new_image_info = get_image_info(dut)
    if new_image_info['current'] != image_type:
        pytest.fail("Rebooting to {} image failed".format(image_type))

    set_next_boot(request, dut)


@pytest.fixture(scope='function')
def setup_images(request, testbed_devices, get_fw_path, components_list):
    """"
    setup part of 'update from next image test' case.
    backup both images files and generate new json files.
    """
    dut = testbed_devices['dut']
    set_next_boot(request, dut)
    image_info = get_image_info(dut)

    platform_type = dut.facts['platform']
    platform_comp_path = '/usr/share/sonic/device/' + platform_type + '/platform_components.json'

    # backup current image platform file
    current_backup_path = os.path.join(BASE_DIR, "current_platform_component_backup.json")
    res = dut.fetch(src=platform_comp_path, dest=current_backup_path, flat="yes")

    # reboot to next image
    reboot_to_image(request, testbed_devices, image_type=image_info['next'])
    # backup next-image platform file
    platform_type = dut.facts['platform']
    platform_comp_path = '/usr/share/sonic/device/' + platform_type + '/platform_components.json'
    next_backup_path = os.path.join(BASE_DIR, "next_platform_component_backup.json")
    dut.fetch(src=platform_comp_path, dest=next_backup_path, flat="yes")
    # generate component file for the next image
    current_component = get_fw_path['current_component']
    comp_path = os.path.join("/home/admin", current_component)
    dut.command("mkdir -p {}".format(comp_path))
    comp_path = os.path.join(comp_path, os.path.basename(get_fw_path['path_to_install']))
    generate_components_file(dut, components_list, get_fw_path['current_component'],
                                comp_path, get_fw_path['previous_ver'])
    # copy fw to dut (next image)
    dut.copy(src=get_fw_path['previous_ver'], dest=comp_path)

    # reboot to first image
    reboot_to_image(request, testbed_devices, image_type=image_info['current'])
    origin_img = image_info['current']
    next_img = image_info['next']

    yield
    # teardown
    new_image_info = get_image_info(dut)
    if new_image_info['current'] == next_img:
        dut.command("rm -rf {}".format(comp_path))
        # restore json file
        dut.copy(src=next_backup_path, dest="/usr/share/sonic/device/{}/platform_components.json".format(platform_type))
        reboot_to_image(request, testbed_devices, image_type=origin_img)
        dut.copy(src=current_backup_path, dest="/usr/share/sonic/device/{}/platform_components.json".format(platform_type))
    else:
        dut.copy(src=current_backup_path, dest="/usr/share/sonic/device/{}/platform_components.json".format(platform_type))
        reboot_to_image(request, testbed_devices, image_type=new_image_info['next'])
        dut.copy(src=next_backup_path, dest="/usr/share/sonic/device/{}/platform_components.json".format(platform_type))
        reboot_to_image(request, testbed_devices, image_type=new_image_info['current'])


def update_from_next_img(request, testbed_devices, get_fw_path, components_list):
    """
    update from next image test case.
    """
    dut = testbed_devices['dut']
    # setup
    request.getfixturevalue('setup_images')

    # generate component file for the current image
    current_component = get_fw_path['current_component']
    comp_path = os.path.join("/home/admin", current_component)
    dut.command("mkdir -p {}".format(comp_path))
    comp_path = os.path.join(comp_path, os.path.basename(get_fw_path['path_to_install']))

    generate_components_file(dut, components_list, get_fw_path['current_component'],
                                comp_path, get_fw_path['version_to_install'])

    dut.copy(src=get_fw_path['path_to_install'], dest=comp_path)
    command = "fwutil update -f -y"
    update_code = execute_update_cmd(request, dut, cmd=command, component=get_fw_path['current_component'],
                        version_to_install=get_fw_path['version_to_install'], expected_log=FW_INSTALL_SUCCESS_LOG)
    if update_code != SUCCESS_CODE:
        pytest.fail("Installation Failed. Aborting!")

    image_info = get_image_info(dut)
    next_boot_cmd = "sonic_installer set_next_boot {}".format(image_info['next'])
    result = dut.command(next_boot_cmd)
    if result['rc'] != SUCCESS_CODE:
        pytest.fail("Could not execute command {}".format(next_boot_cmd))

    next_img_cmd = "fwutil update -y --image=next"

    update_code = execute_update_cmd(request, dut, cmd=next_img_cmd, component=get_fw_path['current_component'],
                        version_to_install=get_fw_path['previous_ver'], expected_log=FW_INSTALL_SUCCESS_LOG)
    dut.command("rm -rf {}".format(comp_path))


def generate_invalid_structure_file(dut, components_list, chassis, platform_type, is_valid_comp_structure):
    """
    Generate invlid platform_components.json file - for negative test cases.
    """
    fw_data = get_output_data(dut)
    json_data = {}
    json_data[chassis] = {}
    json_data[chassis][platform_type] = {}
    json_data[chassis][platform_type]['component'] = {}

    for comp in components_list:
        json_data[chassis][platform_type]['component'][comp] = {}
        json_data[chassis][platform_type]['component'][comp]['firmware'] = 'path/to/install'
        if is_valid_comp_structure is False:
            json_data[chassis][platform_type]['component'][comp]['version'] = {}
            json_data[chassis][platform_type]['component'][comp]['version']['version'] = 'version/to/install'
        else:
            json_data[chassis][platform_type]['component'][comp]['version'] = 'version/to/install'
        json_data[chassis][platform_type]['component'][comp]['info'] = 'description'

    with open(os.path.join(BASE_DIR, "tmp_platform_components.json"), "w") as comp_file:
        json.dump(json_data, comp_file)

    dst = "/usr/share/sonic/device/{}/platform_components.json".format(platform_type)
    dut.copy(src=os.path.join(BASE_DIR, "tmp_platform_components.json"), dest=dst)


def test_show_positive(testbed_devices, components_list):
    """
    Purpose of the test is checking all required components appear in
    'fwutil show status' command output according the given config file.
    :param dut: dut
    :param fw_status: fixture for getting status.
    :param get_output_data: fixture for parsing the status and place in a data structure.
    """
    dut = testbed_devices["dut"]
    fw_data = get_output_data(dut)
    for comp in components_list:
        if comp not in fw_data:
            raise "Missing component {}".format(comp)


def test_install_positive(request, testbed_devices, testbed, get_fw_path):
    """
    performs fw installation from local path.
    """
    dut = testbed_devices['dut']
    # copy fw to dut and install
    comp_path = os.path.join("/tmp", get_fw_path['current_component'])
    dut.command("mkdir -p {}".format(comp_path))
    dut.copy(src=get_fw_path['path_to_install'], dest=comp_path)
    command = "fwutil install chassis component {} fw -y {}".format(get_fw_path['current_component'],
                                            os.path.join(comp_path, os.path.basename(get_fw_path['path_to_install'])))
    install_code = execute_update_cmd(request, dut, cmd=command, component=get_fw_path['current_component'],
                        version_to_install=get_fw_path['version_to_install'], expected_log=FW_INSTALL_SUCCESS_LOG)
    if install_code != SUCCESS_CODE:
        pytest.fail("Installation Failed. Aborting!")

    # recover previous fw only if previous was the latest:
    if get_fw_path['is_latest_installed'] is True:
        dut.command("rm -rf {}".format(comp_path))
        dut.command("mkdir -p {}".format(comp_path))
        dut.copy(src=get_fw_path['current_fw_path'], dest=comp_path)
        command = "fwutil install chassis component {} fw -y {}".format(get_fw_path['current_component'],
                                            os.path.join(comp_path, os.path.basename(get_fw_path['current_fw_path'])))
        install_code = execute_update_cmd(request, dut, cmd=command, component=get_fw_path['current_component'],
                            version_to_install=get_fw_path['previous_ver'], expected_log=FW_INSTALL_SUCCESS_LOG)
        if install_code != SUCCESS_CODE:
            pytest.fail("Installation Failed. Aborting!")


    dut.command("rm -rf {}".format(comp_path))


def test_install_negative(request, testbed_devices, get_fw_path):
    """
    Tries to install invalid FW and checks the expected errors occures.
    """
    dut = testbed_devices["dut"]
    # invalid component name
    cmd = 'fwutil install chassis component {} fw -y {}'.format('UNVALID_FW_NAME', get_fw_path['path_to_install'])
    execute_wrong_command(dut, cmd, UNVALID_NAME_LOG)
    # invalid path
    cmd = 'fwutil install chassis component {} fw -y {}'.format(get_fw_path['current_component'], '/this/is/invalid/url')
    execute_wrong_command(dut, cmd, UNVALID_PATH_LOG)
    # invalid url
    cmd = 'fwutil install chassis component {} fw -y {}'.format(get_fw_path['current_component'], 'http://not/valid/url')
    execute_wrong_command(dut, cmd, UNVALID_URL_LOG)


def test_update_positive(request, testbed_devices, components_list, get_fw_path, backup_platform_file):
    """
    Performs update from current image and from next image.
    NOTICE: The next image should be an image with fwutil feature
    """
    dut = testbed_devices["dut"]
    update_from_current_img(request, dut, get_fw_path, components_list)
    update_from_next_img(request, testbed_devices, get_fw_path, components_list)


def test_update_negative(request, testbed_devices, components_list, backup_platform_file):
    """
    Try to update with wrong platform_components.json file and check errors occure.
    """
    dut = testbed_devices['dut']
    platform_type = dut.facts['platform']
    cmd = "fwutil update -y"
    # invalid platform schema
    generate_invalid_structure_file(dut, components_list, chassis='INVALID_CHASSIS',
                platform_type=platform_type, is_valid_comp_structure=True)
    execute_wrong_command(dut, cmd, INVALID_PLATFORM_SCHEMA_LOG)

    # invalid chassis schema
    generate_invalid_structure_file(dut, components_list, chassis='chassis',
                platform_type='INVALID_PLATFORM', is_valid_comp_structure=True)
    execute_wrong_command(dut, cmd, INVALID_CHASSIS_SCHEMA_LOG)

    # invalid components schema
    generate_invalid_structure_file(dut, components_list, chassis='chassis',
                platform_type=platform_type, is_valid_comp_structure=False)
    execute_wrong_command(dut, cmd, INVALID_COMPONENT_SCHEMA_LOG)
