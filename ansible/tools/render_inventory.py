#!/usr/bin/env python3
import yaml
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_PATH = os.path.join(BASE_DIR, 'vars', 'vm_catalog.yml')
HOSTS_OUT_PATH = os.path.join(BASE_DIR, 'inventory', 'generated_hosts.yml')
PEERS_OUT_PATH = os.path.join(BASE_DIR, 'inventory', 'group_vars', 'generated_peers.yml')

def main():
    if not os.path.exists(CATALOG_PATH):
        print(f"Catalog not found: {CATALOG_PATH}")
        return

    with open(CATALOG_PATH, 'r') as f:
        catalog = yaml.safe_load(f).get('vm_catalog', {})

    hosts_output = {
        'all': {
            'children': {}
        }
    }
    
    peers_dict = {}

    for vm_name, vm_def in catalog.items():
        ansible_groups = vm_def.get('ansible', {}).get('groups', [])
        
        # We need an ipv6 from the networks block
        ipv6 = None
        for net in vm_def.get('networks', []):
            if net.get('name') == 'infra' and 'ipv6' in net:
                ipv6 = net['ipv6']
                break
        
        if ipv6:
            peers_dict[vm_name] = {'ipv6': ipv6}

        for group in ansible_groups:
            if group not in hosts_output['all']['children']:
                hosts_output['all']['children'][group] = {'hosts': {}}
            
            host_entry = {}
            if ipv6:
                host_entry['ansible_host'] = ipv6
            
            # Additional logic for ssh user based on os_family can be added here if needed
            # For now just use the default set in group_vars
            
            hosts_output['all']['children'][group]['hosts'][vm_name] = host_entry

    with open(HOSTS_OUT_PATH, 'w') as f:
        f.write("# GENERATED FROM vm_catalog.yml - DO NOT EDIT\n")
        yaml.dump(hosts_output, f, default_flow_style=False)

    with open(PEERS_OUT_PATH, 'w') as f:
        f.write("# GENERATED FROM vm_catalog.yml - DO NOT EDIT\n")
        yaml.dump({'generated_peers': peers_dict}, f, default_flow_style=False)

    print(f"Rendered {HOSTS_OUT_PATH}")
    print(f"Rendered {PEERS_OUT_PATH}")

if __name__ == '__main__':
    main()
