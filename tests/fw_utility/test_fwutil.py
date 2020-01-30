import os
import re
import pytest
from common import reboot
import yaml
from loganalyzer import LogAnalyzer, LogAnalyzerError
from datetime import datetime
import logging
logger = logging.getLogger(__name__)

FW_UTIL_DATA = {}
COMPONENTS = {'BIOS', 'CPLD'}
BASE_DIR = os.path.dirname(os.path.realpath(__file__))
SUCCESS_CODE = 0


def fw_status(duthost):
    stdout = duthost.command("fwutil show status")
    if stdout['rc'] != SUCCESS_CODE:
        err = stdout['stderr']
        pytest.fail("Could not execute command 'fwutil show status'")
        raise err

    return stdout['stdout']


def parse_status(fw_status):
    output_data = {}
    output_lines = fw_status.splitlines()[2:]  # Skip the header lines in output
    for line in output_lines:
        split_line = re.split(r'\s{2,}', line)
        if split_line[1] not in COMPONENTS:  # There is a value in module column
            chassis = split_line[0]
            module = split_line[1]
            component = split_line[2]
            version = split_line[3]
            desc = split_line[4]
        else:
            chassis = split_line[0]
            module = ''
            component = split_line[1]
            version = split_line[2]
            desc = split_line[3]
        output_data[component] = {
            'chassis': chassis,
            'module': module,
            'version': version,
            'desc': desc
        }
    return output_data


def components_list(duthost, fw_data, config_file):

    # config file contains platform string identifier and components separated by ','.
    # x86_64-mlnx_msn2010-r0: BIOS,CPLD
    conf_path = os.path.join(BASE_DIR, config_file)
    with open(conf_path, "r") as config:
        platforms_dict = yaml.safe_load(config)
        platform_type = duthost.facts['platform']
        components = platforms_dict[platform_type]

    for comp in components.split(","):
        if comp not in fw_data:
            raise "Missing component {}".format(comp)

    return components.split(",")


def test_show_positive(request, duthost):
    """
    Purpose of the test is checking all required components appear in
    'fwutil show status' command output according the given config file.
    :param duthost: dut
    :param fw_status: fixture for getting status.
    :param parse_status: fixture for parsing the status and place in a data structure.
    """
    status_output = fw_status(duthost)
    fw_data = parse_status(status_output)

    config_file = request.config.getoption("--config_file")
    try:
        components_list(duthost, fw_data, config_file)
    except LogAnalyzerError as err:
        raise(err)

