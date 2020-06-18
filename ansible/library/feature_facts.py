#!/usr/bin/env python

# This ansible module is for gathering feature facts from SONiC device.
#
# "show feature" command is used to output all available features and to show what features are enabled.
#
# Example output of "show feature":
#
# Feature             Status
# ------------------  --------
# telemetry           enabled
# sflow               disabled
# what-just-happened  enabled
# 

from ansible.module_utils.basic import *
SUCCESS_CODE = 0


def get_feature_facts(module):
    rc, stdout, stderr = module.run_command("show features")
    if rc != SUCCESS_CODE:
        module.fail_json(msg='Failed to get feature data, rc=%s, stdout=%s, stderr=%s' % (rc, stdout, stderr))
    
    features = {}
    output_lines = stdout.splitlines()[2:]  # Skip the header lines in output
    for line in output_lines:
        feature_line = line.split()
        if len(feature_line) == 2:
            features[feature_line[0]] = feature_line[1]
    
    return features

def main():
    module = AnsibleModule(argument_spec=dict())
    features = get_feature_facts(module)
    module.exit_json(ansible_facts={'feature_facts': features})


if __name__ == '__main__':
    main()
