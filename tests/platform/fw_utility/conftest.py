import os
import pytest
from fwutil_helper import *
from psu_controller import psu_controller
from loganalyzer import LogAnalyzer, LogAnalyzerError
import yaml


@pytest.fixture(scope='function')
def components_list(request, testbed_devices):
    """
    fixture that returns the components list
    according to the given config file.
    :param request
    :param testbed_devices: testbed devices
    """
    dut = testbed_devices["dut"]
    config_file = request.config.getoption("--config_file")
    # config file contains platform string identifier and components separated by ','.
    # e.g.: x86_64-mlnx_msn2010-r0: BIOS,CPLD
    conf_path = os.path.join(BASE_DIR, config_file)
    with open(conf_path, "r") as config:
        platforms_dict = yaml.safe_load(config)
        platform_type = dut.facts['platform']
        components = platforms_dict[platform_type]

    yield components.split(",")


@pytest.fixture(scope='function')
def psu_ctrl(testbed_devices, psu_controller):
    """
    return psu_controller
    """
    dut = testbed_devices["dut"]
    yield psu_controller(dut.hostname, dut.facts["asic_type"])


@pytest.fixture(scope='function')
def backup_platform_file(testbed_devices):
    """
    backup the original platform_components.json file
    """
    dut = testbed_devices["dut"]
    platform_type = dut.facts['platform']
    platform_comp_path = '/usr/share/sonic/device/' + platform_type + '/platform_components.json'
    backup_path = os.path.join(BASE_DIR, "platform_component_backup.json")
    res = dut.fetch(src=platform_comp_path, dest=backup_path, flat="yes")
    yield

    dut.copy(src=backup_path, dest=platform_comp_path)

