#!/usr/bin/env python3
"""
LXD Ansible Dynamic Inventory Script

This script connects to an LXD server and generates Ansible inventory
based on containers and VMs with configurable filtering.

Usage:
    python lxd_inventory.py --list
    python lxd_inventory.py --host <hostname>

Configuration:
    Set environment variables or modify the configuration section below:
    - LXD_ENDPOINT: LXD server endpoint (default: unix socket)
    - LXD_CERT_PATH: Path to client certificate
    - LXD_KEY_PATH: Path to client private key
    - LXD_CA_CERT_PATH: Path to CA certificate
    - LXD_VERIFY_SSL: Whether to verify SSL certificates
"""

import argparse
import json
import os
import sys
import yaml
from typing import Dict, List, Any, Optional
import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Suppress SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class LXDInventory:
    def __init__(self, args=None):
        self.args = args
        self.debug = args and args.debug
        self.config = self._load_config()
        self.session = self._create_session()
        
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from environment variables, CLI args, or defaults."""
        config = {
            'verify_ssl': os.getenv('LXD_VERIFY_SSL', 'false').lower() == 'true',
            'cert_path': os.getenv('LXD_CERT_PATH'),
            'key_path': os.getenv('LXD_KEY_PATH'),
            'ca_cert_path': os.getenv('LXD_CA_CERT_PATH'),
        }
        
        # LXD endpoint/host - CLI --host takes precedence
        if self.args and self.args.host:
            # If --host is provided, construct the endpoint
            host = self.args.host
            if not host.startswith(('http://', 'https://', 'unix://')):
                # Default to https if no protocol specified
                host = f"https://{host}:8443"
            config['endpoint'] = host
        else:
            config['endpoint'] = os.getenv('LXD_ENDPOINT', 'unix:///var/lib/lxd/unix.socket')
        
        # Handle filters with CLI args taking precedence
        filters = {}
        
        # Status filter
        if self.args and self.args.status:
            filters['status'] = [self.args.status]
        else:
            env_status = os.getenv('LXD_FILTER_STATUS', 'running,stopped,frozen,error')
            filters['status'] = env_status.split(',') if env_status else ['running', 'stopped', 'frozen', 'error']
        
        # Type filter - map CLI args to LXD types
        if self.args and self.args.type:
            type_map = {'vm': 'virtual-machine', 'lxc': 'container'}
            cli_types = [t.strip() for t in self.args.type.split(',')]
            filters['type'] = [type_map.get(t, t) for t in cli_types]
        else:
            env_types = os.getenv('LXD_FILTER_TYPE', 'container,virtual-machine').split(',')
            filters['type'] = env_types
        
        # Project filter
        if self.args and self.args.all_projects:
            filters['projects'] = ['all']
        elif self.args and self.args.project:
            filters['projects'] = [p.strip() for p in self.args.project.split(',')]
        else:
            env_project = os.getenv('LXD_FILTER_PROJECT', 'default')
            filters['projects'] = [env_project] if env_project else ['default']
        
        # Profile filter
        if self.args and self.args.profile:
            filters['profiles'] = [p.strip() for p in self.args.profile.split(',')]
        else:
            env_profiles = os.getenv('LXD_FILTER_PROFILES', '')
            filters['profiles'] = env_profiles.split(',') if env_profiles else []
        
        # Ignore interfaces filter
        if self.args and self.args.ignore_interface:
            filters['ignore_interfaces'] = [i.strip() for i in self.args.ignore_interface.split(',')]
        else:
            env_ignore = os.getenv('LXD_IGNORE_INTERFACES', 'lo,docker0,cilium_host,cilium_vxlan,cilium_net')
            filters['ignore_interfaces'] = env_ignore.split(',') if env_ignore else ['lo']
        
        # Exclude names (only from env for now)
        filters['exclude_names'] = os.getenv('LXD_EXCLUDE_NAMES', '').split(',') if os.getenv('LXD_EXCLUDE_NAMES') else []
        
        config['filters'] = filters
        return config
    
    def _create_session(self) -> requests.Session:
        """Create a requests session with appropriate configuration."""
        session = requests.Session()
        
        # Configure retries
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # Configure SSL/TLS
        if self.config['cert_path'] and self.config['key_path']:
            session.cert = (self.config['cert_path'], self.config['key_path'])
        
        if self.config['ca_cert_path']:
            session.verify = self.config['ca_cert_path']
        else:
            session.verify = self.config['verify_ssl']
        
        return session
    
    def _make_request(self, path: str) -> Dict[str, Any]:
        """Make a request to the LXD API."""
        if self.config['endpoint'].startswith('unix://'):
            # Unix socket connection
            import requests_unixsocket
            session = requests_unixsocket.Session()
            url = self.config['endpoint'].replace('unix://', 'http+unix://') + '/1.0' + path
        else:
            # HTTP/HTTPS connection
            session = self.session
            url = f"{self.config['endpoint']}/1.0{path}"
        
        try:
            response = session.get(url)
            response.raise_for_status()
            data = response.json()
            
            if data.get('type') == 'error':
                raise Exception(f"LXD API error: {data.get('error', 'Unknown error')}")
            
            return data.get('metadata', {})
        except requests.exceptions.RequestException as e:
            print(f"Error connecting to LXD: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    
    def _get_instances(self) -> List[Dict[str, Any]]:
        """Get all instances from LXD with detailed information."""
        all_instances = []
        projects = self.config['filters']['projects']
        
        if 'all' in projects:
            # Get list of all projects first
            try:
                if self.debug:
                    print(f"Debug: Fetching all projects...", file=sys.stderr)
                    
                # Try different approaches to get project names
                projects_data = self._make_request("/projects?recursion=0")
                
                if self.debug:
                    print(f"Debug: Projects API response type: {type(projects_data)}", file=sys.stderr)
                    print(f"Debug: Projects API response: {projects_data[:3] if isinstance(projects_data, list) else projects_data}", file=sys.stderr)
                
                if isinstance(projects_data, list):
                    # Extract project names from URLs like '/1.0/projects/default'
                    projects = [url.split('/')[-1] for url in projects_data if '/projects/' in url]
                else:
                    # If recursion=0 doesn't work, try without recursion parameter
                    try:
                        projects_data = self._make_request("/projects")
                        if isinstance(projects_data, dict):
                            projects = list(projects_data.keys())
                        elif isinstance(projects_data, list):
                            projects = [url.split('/')[-1] for url in projects_data if '/projects/' in url]
                        else:
                            projects = ['default']
                    except:
                        projects = ['default']
                
                # If we still don't have projects, fall back to default
                if not projects:
                    projects = ['default']
                    
                if self.debug:
                    print(f"Debug: Found projects: {projects}", file=sys.stderr)
                    
            except Exception as e:
                print(f"Warning: Could not fetch all projects, using default: {e}", file=sys.stderr)
                projects = ['default']
        
        for project in projects:
            try:
                if self.debug:
                    print(f"Debug: Fetching instances from project '{project}'...", file=sys.stderr)
                    
                path = f"/instances?recursion=2&project={project}"
                instances = self._make_request(path)
                
                if self.debug:
                    print(f"Debug: Found {len(instances)} instances in project '{project}'", file=sys.stderr)
                
                # Add project info to each instance
                for instance in instances:
                    instance['lxd_project'] = project
                all_instances.extend(instances)
            except Exception as e:
                print(f"Warning: Could not fetch instances from project '{project}': {e}", file=sys.stderr)
                continue
        
        if self.debug:
            print(f"Debug: Total instances found: {len(all_instances)}", file=sys.stderr)
        
        return all_instances
    
    def _filter_instance(self, instance: Dict[str, Any]) -> bool:
        """Apply filters to determine if an instance should be included."""
        # Filter by status
        status_filter = [s.lower().strip() for s in self.config['filters']['status']]
        if status_filter and 'all' not in status_filter:
            if instance['status'].lower() not in status_filter:
                return False
        
        # Filter by type
        type_filter = self.config['filters']['type']
        if type_filter and 'all' not in type_filter and instance['type'] not in type_filter:
            return False
        
        # Filter by profiles
        profile_filter = self.config['filters']['profiles']
        if profile_filter and not any(profile in instance.get('profiles', []) for profile in profile_filter):
            return False
        
        # Exclude by name
        exclude_names = self.config['filters']['exclude_names']
        if exclude_names and instance['name'] in exclude_names:
            return False
        
        return True
    
    def _get_instance_ip(self, instance: Dict[str, Any]) -> Optional[str]:
        """Extract the primary IP address from an instance."""
        state = instance.get('state', {})
        if not state:
            return None
            
        network = state.get('network')
        if not network or not isinstance(network, dict):
            return None
        
        ignore_interfaces = self.config['filters']['ignore_interfaces']
        if self.debug:
            print(f"Debug: Ignoring interfaces: {ignore_interfaces}", file=sys.stderr)
        
        # Look for the first non-ignored IPv4 address
        for interface_name, interface_data in network.items():
            if interface_name in ignore_interfaces:
                if self.debug:
                    print(f"Debug: Skipping ignored interface: {interface_name}", file=sys.stderr)
                continue
            
            if not isinstance(interface_data, dict):
                continue
                
            addresses = interface_data.get('addresses', [])
            if not isinstance(addresses, list):
                continue
                
            for addr in addresses:
                if isinstance(addr, dict) and addr.get('family') == 'inet' and addr.get('scope') == 'global':
                    if self.debug:
                        print(f"Debug: Found IP {addr.get('address')} on interface {interface_name}", file=sys.stderr)
                    return addr.get('address')
        
        return None
    
    def _generate_inventory(self) -> Dict[str, Any]:
        """Generate the Ansible inventory."""
        instances = self._get_instances()
        inventory = {
            '_meta': {
                'hostvars': {}
            }
        }
        
        # Group instances
        groups = {
            'all': {'hosts': []},
            'lxd_containers': {'hosts': []},
            'lxd_vms': {'hosts': []},
            'lxd_running': {'hosts': []},
            'lxd_stopped': {'hosts': []},
            'lxd_frozen': {'hosts': []},
            'lxd_error': {'hosts': []},
        }
        
        for instance in instances:
            if not self._filter_instance(instance):
                continue
            
            name = instance['name']
            ip_address = self._get_instance_ip(instance)
            
            # Add to main groups
            groups['all']['hosts'].append(name)
            
            # Add to type-specific groups
            if instance['type'] == 'container':
                groups['lxd_containers']['hosts'].append(name)
            elif instance['type'] == 'virtual-machine':
                groups['lxd_vms']['hosts'].append(name)
            
            # Add to status-specific groups
            status = instance['status'].lower()
            if status == 'running':
                groups['lxd_running']['hosts'].append(name)
            elif status == 'stopped':
                groups['lxd_stopped']['hosts'].append(name)
            elif status == 'frozen':
                groups['lxd_frozen']['hosts'].append(name)
            elif status == 'error':
                groups['lxd_error']['hosts'].append(name)
            
            # Create profile-based groups
            for profile in instance.get('profiles', []):
                group_name = f'lxd_profile_{profile}'
                if group_name not in groups:
                    groups[group_name] = {'hosts': []}
                groups[group_name]['hosts'].append(name)
            
            # Add host variables
            hostvars = {
                'lxd_name': name,
                'lxd_type': instance['type'],
                'lxd_status': instance['status'],
                'lxd_architecture': instance['architecture'],
                'lxd_profiles': instance.get('profiles', []),
                'lxd_project': instance.get('lxd_project', 'default'),
            }
            
            if ip_address:
                hostvars['ansible_host'] = ip_address
                hostvars['lxd_ip'] = ip_address
            
            # Add configuration details
            config = instance.get('config', {})
            if config:
                hostvars['lxd_config'] = config
            
            # Add expanded config (e.g., image info)
            expanded_config = instance.get('expanded_config', {})
            if expanded_config:
                hostvars['lxd_expanded_config'] = expanded_config
            
            inventory['_meta']['hostvars'][name] = hostvars
        
        # Remove empty groups
        groups = {k: v for k, v in groups.items() if v['hosts']}
        inventory.update(groups)
        
        return inventory
    
    def get_instance_vars(self, instance_name: str) -> Dict[str, Any]:
        """Get variables for a specific instance."""
        inventory = self._generate_inventory()
        return inventory['_meta']['hostvars'].get(instance_name, {})
    
    def list_inventory(self) -> str:
        """Return the full inventory as JSON."""
        return json.dumps(self._generate_inventory(), indent=2)


def main():
    parser = argparse.ArgumentParser(description='LXD Ansible Dynamic Inventory')
    parser.add_argument('--list', action='store_true', help='List all hosts')
    parser.add_argument('--instance', help='Get variables for a specific instance')
    parser.add_argument('--yaml', action='store_true', help='Output in YAML format')
    
    # LXD connection
    parser.add_argument('--host', help='LXD host/cluster to connect to (overrides LXD_ENDPOINT)')
    
    # Filtering arguments
    parser.add_argument('--status', choices=['running', 'stopped', 'frozen', 'error'], 
                       help='Filter by instance status')
    parser.add_argument('--type', help='Filter by type (vm,lxc) - comma separated')
    parser.add_argument('--project', help='Filter by project(s) - comma separated')
    parser.add_argument('--all-projects', action='store_true', 
                       help='Include all projects (overrides --project)')
    parser.add_argument('--profile', help='Filter by profile(s) - comma separated')
    parser.add_argument('--ignore-interface', help='Interfaces to ignore when finding IP (comma separated)')
    parser.add_argument('--debug', action='store_true', 
                       help='Enable debug output')
    
    args = parser.parse_args()
    
    inventory = LXDInventory(args)
    
    if args.list:
        if args.yaml:
            data = inventory._generate_inventory()
            print(yaml.dump(data, default_flow_style=False))
        else:
            print(inventory.list_inventory())
    elif args.instance:
        instance_vars = inventory.get_instance_vars(args.instance)
        if args.yaml:
            print(yaml.dump(instance_vars, default_flow_style=False))
        else:
            print(json.dumps(instance_vars, indent=2))
    else:
        parser.print_help()


if __name__ == '__main__':
    main()