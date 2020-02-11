import os
import re
import pytest
import time
import sys
import json
import requests
import random
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
    versions = get_versions(dut, location)
    # pick arbitrary componenet
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


def execute_install(request, dut, component, path_to_install, version_to_install):
    cmd = "fwutil install chassis component {} fw -y {}".format(component, path_to_install)

    loganalyzer = LogAnalyzer(ansible_host=dut, marker_prefix='acl')
    loganalyzer.load_common_config()
    try:
        loganalyzer.except_regex = [FW_INSTALL_SUCCESS_LOG]
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

    dut = testbed_devices['dut']
    # copy fw to dut and install
    comp_path = os.path.join(TMP_DIR, get_fw_path['current_component'])
    dut.command("mkdir -p {}".format(comp_path))
    dut.copy(src=get_fw_path['path_to_install'], dest=comp_path)

    install_code = execute_install(request, dut, component=get_fw_path['current_component'],
                        path_to_install=os.path.join(comp_path, os.path.basename(get_fw_path['path_to_install'])),
                        version_to_install=get_fw_path['version_to_install'])
    if install_code != SUCCESS_CODE:
        pytest.fail("Installation Failed. Aborting!")

    # recover previous fw only if previous was the latest:
    if get_fw_path['is_latest_installed'] == True:
        dut.command("rm -rf {}".format(comp_path))
        dut.command("mkdir -p {}".format(comp_path))
        dut.copy(src=get_fw_path['current_fw_path'], dest=comp_path)

        install_code = execute_install(request, dut, component=get_fw_path['current_component'],
                            path_to_install=os.path.join(comp_path, os.path.basename(get_fw_path['current_fw_path'])),
                            version_to_install=get_fw_path['previous_ver'])
        if install_code != SUCCESS_CODE:
            pytest.fail("Installation Failed. Aborting!")

    yield
    dut.command("rm -rf {}".format(comp_path))


def execute_wrong_install(dut, comp_name, comp_path, expected_log):
    """
    execute wrong commands for installation and check the right errors appear.
    """
    cmd = 'fwutil install chassis component {} fw -y {}'.format(comp_name, comp_path)
    result = dut.command(cmd, module_ignore_errors=True)
    if result['rc'] == SUCCESS_CODE:
        pytest.fail("Expected error code!")

    if not result['stderr'].find(expected_log):
        if not result['stdout'].find(expected_log):
            pytest.fail("Expected logs didn't occure!")


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


def test_install_positive(request):
    request.getfixturevalue('install_local')


def test_install_negative(request, testbed_devices, get_fw_path):
    dut = testbed_devices["dut"]
    # invalid component name
    execute_wrong_install(dut, comp_name='UNVALID_FW_NAME', comp_path=get_fw_path['path_to_install'], expected_log=UNVALID_NAME_LOG)
    # invalid path
    execute_wrong_install(dut, comp_name=get_fw_path['current_component'], comp_path='/this/is/invalid/url', expected_log=UNVALID_PATH_LOG)
    # invalid url
    execute_wrong_install(dut, comp_name=get_fw_path['current_component'], comp_path='http://not/valid/url', expected_log=UNVALID_URL_LOG)
