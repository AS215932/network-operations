#!/usr/bin/env python3
import yaml
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_PATH = os.path.join(BASE_DIR, 'vars', 'vm_catalog.yml')
DOCS_OUT_PATH = os.path.join(BASE_DIR, '..', 'docs', 'network-flows.md')

def main():
    if not os.path.exists(CATALOG_PATH):
        print(f"Catalog not found: {CATALOG_PATH}")
        return

    with open(CATALOG_PATH, 'r') as f:
        catalog = yaml.safe_load(f).get('vm_catalog', {})

    with open(DOCS_OUT_PATH, 'a') as f:
        f.write("\n## Generated from vm_catalog.yml flows\n\n")
        
        for vm_name, vm_def in catalog.items():
            flows = vm_def.get('flows', {})
            inbound = flows.get('inbound', [])
            outbound = flows.get('outbound', [])
            
            if inbound or outbound:
                f.write(f"### {vm_name}\n\n")
                if inbound:
                    f.write("| From | Proto | Port | Purpose |\n")
                    f.write("|------|-------|------|---------|\n")
                    for flow in inbound:
                        src = flow.get('source_host') or flow.get('source_group') or flow.get('source', 'any')
                        f.write(f"| {src} | {flow['proto'].upper()} | {flow['port']} | {flow['reason']} |\n")
                    f.write("\n")
                
                if outbound:
                    f.write("**Outbound (cross-cutting):** ")
                    out_list = []
                    for flow in outbound:
                        dst = flow.get('dest_host') or flow.get('dest', 'any')
                        out_list.append(f"{vm_name} → {dst} {flow['proto'].upper()}/{flow['port']} ({flow['reason']})")
                    f.write(", ".join(out_list) + ".\n\n")

    print(f"Appended flows to {DOCS_OUT_PATH}")

if __name__ == '__main__':
    main()
