import pytest
import logging
import scapy
import re
from drop_packets import *

logger = logging.getLogger(__name__)

protocols = {
    '0x6'   : 'tcp',
    '0x11'  : 'udp',
    '0x2'   : 'igmp',
    '0x4'   : 'ipencap'
}


def parse_wjh_table(table):
    entries = []
    headers = []
    header_lines_num = 2
    table_lines = table.splitlines()
    if not table_lines:
        return table_lines

    # check separators index
    for sep_index in range(len(table_lines)):
        if table_lines[sep_index][0] == '-':
            break

    separators = re.split(r'\s{2,}', table.splitlines()[sep_index])[0].split()  # separators between headers and content
    headers_line = table_lines[0]
    start = 0
    # separate headers by table separators
    for sep in separators:
        curr_len = len(sep)
        headers.append(headers_line[start:start+curr_len].strip())
        start += curr_len + 1
    # check if headers appears in next line as well (only for Drop Group header)
    if table_lines[1].strip() == "Group":
        headers[11] = headers[11] + " Group"
        header_lines_num = 3
    output_lines = table.splitlines()[header_lines_num:]  # Skip the header lines in output

    for line in output_lines:
        # if the previous line was too long and has splitted to 2 lines
        if line[0] == " ":
            start_index = len(line) - len(line.lstrip()) + 1
            sep_len = 0
            for j in range(len(separators)):
                sep = separators[j]
                sep_len += len(sep)
                if start_index <= sep_len:
                    break
            # j is the index in the entry
            start_index -= 1
            if entries[-1][headers[j]].endswith(']'):
                space = ''
                first_space_index = line[start_index:].index(' ')
                entries[-1][headers[j]] = entries[-1][headers[j]] + space + line[start_index:start_index + first_space_index].strip()
            else:
                space = ' '
                entries[-1][headers[j]] = entries[-1][headers[j]] + space + line.strip()
            continue

        entry = {}
        data = []
        start = 0
        for sep in separators:
            curr_len = len(sep)
            data.append(line[start:start + curr_len].strip())
            start += curr_len + 1

        for i in range(len(data)):
            entry[headers[i]] = data[i]
        entries.append(entry)

    return entries


def get_table_output(duthost):
    stdout = duthost.command("show what-just-happened")
    if stdout['rc'] != 0:
        raise Exception(stdout['stdout'] + stdout['stderr'])
    table_output = parse_wjh_table(stdout['stdout'])
    return table_output


def verify_drop_on_wjh_table(duthost, pkt, ports_info, sniff_ports):
    table = get_table_output(duthost)
    entry_found = False
    ip_key = 'IP'
    proto_key = 'proto'
    if ip_key not in pkt:
        ip_key = 'IPv6'
        proto_key = 'nh'

    for entry in table:
        src_ip_port = entry['Src IP:Port'].rsplit(':', 1)
        dst_ip_port = entry['Dst IP:Port'].rsplit(':', 1)
        if (pkt.dst == entry['dMAC'] and
            pkt.src == entry['sMAC'] and
            pkt[ip_key].src.lower() == src_ip_port[0].replace('[', '').replace(']', '').lower() and
            pkt[ip_key].dst.lower() == dst_ip_port[0].replace('[', '').replace(']', '').lower()):

                if 'TCP' in pkt:
                    if (str(pkt['TCP'].sport) != src_ip_port[1] or
                        str(pkt['TCP'].dport) != dst_ip_port[1]):
                            continue

                if proto_key == 'proto':
                    if (protocols[hex(pkt[ip_key].proto)] == entry['IP Proto']):
                        entry_found = True
                        break
                else:
                    if (protocols[hex(pkt[ip_key].nh)] == entry['IP Proto']):
                        entry_found = True
                        break

    return entry_found


def send_packets(pkt, duthost, ptfadapter, ptf_tx_port_id):
    # Clear packets buffer on PTF
    ptfadapter.dataplane.flush()
    time.sleep(1)
    # Send packets
    testutils.send(ptfadapter, ptf_tx_port_id, pkt)
    time.sleep(1)


@pytest.fixture(scope='module')
def do_test():
    def do_wjh_test(discard_group, pkt, ptfadapter, duthost, ports_info, sniff_ports, tx_dut_ports=None, comparable_pkt=None):
        # send packet
        send_packets(pkt, duthost, ptfadapter, ports_info["ptf_tx_port_id"])
        # verify packet is dropped
        if comparable_pkt:
            pkt = comparable_pkt
        exp_pkt = expected_packet_mask(pkt)
        testutils.verify_no_packet_any(ptfadapter, exp_pkt, ports=sniff_ports)
        # verify wjh table
        if not verify_drop_on_wjh_table(duthost, pkt, ports_info, sniff_ports):
            pytest.fail("Drop hasn't found in WJH table.")

    return do_wjh_test


@pytest.fixture(scope='module', autouse=True)
def check_global_configuration(duthost):
    stdout = duthost.command("show what-just-happened configuration global")
    if stdout['rc'] != 0:
        raise Exception(stdout['stdout'] + stdout['stderr'])

    stdout = stdout['stdout']
    output_lines = stdout.splitlines()[2:]  # Skip the header lines in output
    for line in output_lines:
        config_line = line.split()
        if config_line[0] == 'debug':
            return

    pytest.skip("Debug mode is not enabled. Skipping test.")


def parse_channel_table(table):
    num_spaces = 2
    output_data = {}
    table_lines = table.splitlines()
    separators = re.split(r'\s{2,}', table.splitlines()[1])  # separators between headers and content
    headers_line = table_lines[0]
    headers = []
    start = 0
    for sep in separators:
        curr_len = len(sep)
        headers.append(headers_line[start:start+curr_len].strip())
        start += curr_len + num_spaces

    # skip header lines
    output_lines = table.splitlines()[2:]
    for line in output_lines:
        data = []
        start = 0
        for i in range(len(separators)):
            sep = separators[i]
            curr_len = len(sep)
            output_data[headers[i]] = line[start:start+curr_len].strip()
            start += curr_len + num_spaces

    return output_data


# to be used for checking channels
@pytest.fixture(scope='module')
def get_channel_configuration(duthost):
    channels = []
    stdout = duthost.command("show what-just-happened configuration channels")
    if stdout['rc'] != 0:
        raise Exception(stdout['stdout'] + stdout['stderr'])

    channels = parse_channel_table(stdout['stdout'])
    return channels


@pytest.fixture(scope='module', autouse=True)
def check_feature_enabled(duthost):
    features = duthost.feature_facts()['ansible_facts']['feature_facts']
    if 'what-just-happened' not in features or features['what-just-happened'] != 'enabled':
        pytest.skip("what-just-happened feature is not available. Skipping the test.")
