import pytest
import logging
import scapy
import re
from drop_packets import *
from ptf.testutils import *

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
        exp_pkt = expected_packet_mask(pkt)
        testutils.verify_no_packet_any(ptfadapter, exp_pkt, ports=sniff_ports)
        # verify wjh table
        if comparable_pkt:
            pkt = comparable_pkt
        if not verify_drop_on_wjh_table(duthost, pkt, ports_info, sniff_ports):
            pytest.fail("Drop hasn't found in WJH table.")

    return do_wjh_test


@pytest.fixture(scope='module', autouse=True)
def check_global_configuration(duthost):
    global_conf = {}
    wjh_global = duthost.shell('/usr/bin/redis-cli -n 4 hgetall "WJH|global"', module_ignore_errors=False)['stdout_lines']
    pytest_assert(wjh_global is not None, "WJH|global does not exist in config_db")

    global_iter = iter(range(len(wjh_global)))
    for i in global_iter:
        global_conf[wjh_global[i]] = wjh_global[i+1]
        next(global_iter, None)

    if global_conf['mode'] != 'debug':
        pytest.skip("Debug mode is not enabled. Skipping test.")


# to be used for checking channels
@pytest.fixture(scope='module', autouse=True)
def get_channel_configuration(duthost):
    channel_conf = {}
    forwarding = duthost.shell('/usr/bin/redis-cli -n 4 hgetall "WJH_CHANNEL|forwarding"', module_ignore_errors=False)['stdout_lines']
    pytest_assert(forwarding is not None, "WJH_CHANNEL|forwarding does not exist in config_db")

    channels_iter = iter(range(len(forwarding)))
    for i in channels_iter:
        channel_conf[forwarding[i]] = forwarding[i+1]
        next(channels_iter, None)

    return channel_conf


@pytest.fixture(scope='module', autouse=True)
def check_feature_enabled(duthost):
    features = duthost.feature_facts()['ansible_facts']['feature_facts']
    if 'what-just-happened' not in features or features['what-just-happened'] != 'enabled':
        pytest.skip("what-just-happened feature is not available. Skipping the test.")


def test_tunnel_ip_in_ip(do_test, ptfadapter, duthost, setup, fanouthost, pkt_fields, ports_info):
    src_ip = pkt_fields['ipv4_src']
    dst_ip = pkt_fields['ipv4_dst']

    # gather facts
    dscp_range = list(range(0, 33))
    ttl_range = list(range(2, 65))
    router_mac = ports_info['dst_mac']
    src_mac = ports_info['src_mac']
    dst_mac = '11:22:33:44:55'
    dscp_in_idx = 0
    dscp_out_idx = len(dscp_range) / 2
    ttl_in_idx = 0
    ttl_out_idx = len(ttl_range) / 2

    dscp_in = dscp_range[dscp_in_idx]
    tos_in = dscp_in << 2
    dscp_out = dscp_range[dscp_out_idx]
    tos_out = dscp_out << 2


    ecn_in = 0
    ecn_out = 2
    ttl_in = ttl_range[ttl_in_idx]
    ttl_in |= ecn_in
    ttl_out = ttl_range[ttl_out_idx]
    ttl_out |= ecn_out

    exp_tos = tos_out
    exp_ttl = ttl_out - 1

    inner_src_ip = '1.1.1.1'

    inner_packet = simple_ip_only_packet(
                ip_dst=dst_ip,
                ip_src=inner_src_ip,
                ip_ttl=ttl_in,
                ip_tos=tos_in
    )

    exp_pkt = Ether(dst=dst_mac, src=router_mac) / inner_packet
    exp_pkt['IP'].tos = exp_tos
    exp_pkt['IP'].ttl = exp_ttl

    pkt = simple_ipv4ip_packet(
        eth_dst=router_mac,
        eth_src=src_mac,
        ip_src='0.0.0.0',
        ip_dst=dst_ip,
        ip_tos=tos_out,
        ip_ttl=ttl_out,
        inner_frame=inner_packet
    )

    do_test("L3", pkt, ptfadapter, duthost, ports_info, setup['neighbor_sniff_ports'])
