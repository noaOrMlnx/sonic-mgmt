import os
import re
import pytest
import json
import random
from fwutil_helper import *
from loganalyzer import LogAnalyzer, LogAnalyzerError

import logging
logger = logging.getLogger(__name__)


def test_show_positive(testbed_devices, components_list):
    """
    The purpose of the test is to check that all required components appear in
    'fwutil show status' command output, according the given vendor config file.
    :param testbed_devices
    :param components_list: fixture with all expected components
    """
    dut = testbed_devices['dut']
    fw_data = get_output_data(dut)
    for comp in components_list:
        if comp not in fw_data:
            raise "Missing component {}".format(comp)


def test_install_positive(request, testbed_devices, testbed, get_fw_path, component_object):
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
    try:
        install_code = execute_update_cmd(request, dut, cmd=command, component=get_fw_path['current_component'],
                        version_to_install=get_fw_path['version_to_install'], component_object=component_object, expected_log=FW_INSTALL_SUCCESS_LOG)
        if install_code != SUCCESS_CODE:
            dut.command("rm -rf {}".format(comp_path))
            pytest.fail("Installation Failed. Aborting!")

        # recover previous fw only if previous was the latest:
        if get_fw_path['is_latest_installed'] is True:
            dut.command("rm -rf {}".format(comp_path))
            dut.command("mkdir -p {}".format(comp_path))
            dut.copy(src=get_fw_path['current_fw_path'], dest=comp_path)
            command = "fwutil install chassis component {} fw -y {}".format(get_fw_path['current_component'],
                                                os.path.join(comp_path, os.path.basename(get_fw_path['current_fw_path'])))
            install_code = execute_update_cmd(request, dut, cmd=command, component=get_fw_path['current_component'],
                                version_to_install=get_fw_path['previous_ver'], component_object=component_object, expected_log=FW_INSTALL_SUCCESS_LOG)
            if install_code != SUCCESS_CODE:
                dut.command("rm -rf {}".format(comp_path))
                pytest.fail("Installation Failed. Aborting!")

    except Exception:
        "Failed to install localy"

    finally:
        dut.command("rm -rf {}".format(comp_path))


@pytest.mark.disable_loganalyzer
def test_install_negative(request, testbed_devices, get_fw_path):
    """
    Tries to install invalid FW and checks the expected errors occures.
    """
    dut = testbed_devices['dut']
    # invalid component name
    cmd = 'fwutil install chassis component {} fw -y {}'.format('UNVALID_FW_NAME', get_fw_path['path_to_install'])
    execute_wrong_command(dut, cmd, UNVALID_NAME_LOG)
    # invalid path
    cmd = 'fwutil install chassis component {} fw -y {}'.format(get_fw_path['current_component'], '/this/is/invalid/url')
    execute_wrong_command(dut, cmd, UNVALID_PATH_LOG)
    # invalid url
    cmd = 'fwutil install chassis component {} fw -y {}'.format(get_fw_path['current_component'], 'http://not/valid/url')
    execute_wrong_command(dut, cmd, UNVALID_URL_LOG)


def test_update_positive(request, testbed_devices, components_list, component_object, get_fw_path, backup_platform_file):
    """
    Performs update from current image and from next image.
    NOTICE: The next image should be an image with fwutil feature
    """
    dut = testbed_devices['dut']
    update_from_current_img(request, dut, get_fw_path, components_list, component_object)
    update_from_next_img(request, testbed_devices, get_fw_path, components_list, component_object)


@pytest.mark.disable_loganalyzer
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
