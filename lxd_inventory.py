#!/usr/bin/env python3
"""
LXD Ansible Dynamic Inventory Script

This script connects to an LXD server and generates Ansible inventory
based on containers and VMs with configurable filtering.

Usage:
    python lxd_inventory.py --list
    python lxd_inventory.py --instance <instancename>

Configuration:
    Create a YAML configuration file (lxd_inventory.yml) or specify with --config.
    The script looks for config files in this order:
    - ./lxd_inventory.yml
    - ./lxd_inventory.yaml  
    - ~/.config/lxd_inventory.yml
    - ~/.config/lxd_inventory.yaml
    - /etc/lxd_inventory.yml
    - /etc/lxd_inventory.yaml
    
    CLI arguments override YAML configuration settings.
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
        """Load configuration from YAML file and CLI args, with defaults."""
        # Load from YAML config file first
        config_data = self._load_yaml_config()
        
        # Base configuration
        config = {
            'verify_ssl': config_data.get('verify_ssl', False),
            'cert_path': config_data.get('cert_path'),
            'key_path': config_data.get('key_path'),
            'ca_cert_path': config_data.get('ca_cert_path'),
        }
        
        # LXD endpoint/host - CLI --host takes precedence, then config file, then default
        if self.args and self.args.host:
            # If --host is provided, construct the endpoint
            host = self.args.host
            if not host.startswith(('http://', 'https://', 'unix://')):
                # Default to https if no protocol specified
                host = f"https://{host}:8443"
            config['endpoint'] = host
        else:
            config['endpoint'] = config_data.get('endpoint', 'unix:///var/lib/lxd/unix.socket')
        
        # Handle filters with CLI args taking precedence, then config file, then defaults
        filters = {}
        
        # Status filter
        if self.args and self.args.status:
            filters['status'] = [self.args.status]
        else:
            config_status = config_data.get('filters', {}).get('status')
            if config_status:
                filters['status'] = config_status if isinstance(config_status, list) else config_status.split(',')
            else:
                filters['status'] = ['running', 'stopped', 'frozen', 'error']
        
        # Type filter - map CLI args to LXD types
        if self.args and self.args.type:
            type_map = {'vm': 'virtual-machine', 'lxc': 'container'}
            cli_types = [t.strip() for t in self.args.type.split(',')]
            filters['type'] = [type_map.get(t, t) for t in cli_types]
        else:
            config_types = config_data.get('filters', {}).get('type')
            if config_types:
                filters['type'] = config_types if isinstance(config_types, list) else config_types.split(',')
            else:
                filters['type'] = ['container', 'virtual-machine']
        
        # Project filter
        if self.args and self.args.all_projects:
            filters['projects'] = ['all']
        elif self.args and self.args.project:
            filters['projects'] = [p.strip() for p in self.args.project.split(',')]
        else:
            config_projects = config_data.get('filters', {}).get('projects')
            if config_projects:
                if 'all' in config_projects or config_projects == 'all':
                    filters['projects'] = ['all']
                else:
                    filters['projects'] = config_projects if isinstance(config_projects, list) else config_projects.split(',')
            else:
                filters['projects'] = ['default']
        
        # Profile filter
        if self.args and self.args.profile:
            filters['profiles'] = [p.strip() for p in self.args.profile.split(',')]
        else:
            config_profiles = config_data.get('filters', {}).get('profiles')
            if config_profiles:
                filters['profiles'] = config_profiles if isinstance(config_profiles, list) else config_profiles.split(',')
            else:
                filters['profiles'] = []
        
        # Ignore interfaces filter
        if self.args and self.args.ignore_interface:
            filters['ignore_interfaces'] = [i.strip() for i in self.args.ignore_interface.split(',')]
        else:
            config_ignore = config_data.get('filters', {}).get('ignore_interfaces')
            if config_ignore:
                filters['ignore_interfaces'] = config_ignore if isinstance(config_ignore, list) else config_ignore.split(',')
            else:
                filters['ignore_interfaces'] = ['lo', 'docker0', 'cilium_host', 'cilium_vxlan', 'cilium_net']
        
        # IPv6 preference
        if self.args and self.args.prefer_ipv6:
            filters['prefer_ipv6'] = True
        else:
            config_ipv6 = config_data.get('filters', {}).get('prefer_ipv6')
            if config_ipv6 is not None:
                filters['prefer_ipv6'] = config_ipv6
            else:
                filters['prefer_ipv6'] = False
        
        # Exclude names - from config file only
        config_exclude = config_data.get('filters', {}).get('exclude_names')
        if config_exclude:
            filters['exclude_names'] = config_exclude if isinstance(config_exclude, list) else config_exclude.split(',')
        else:
            filters['exclude_names'] = []
        
        config['filters'] = filters
        return config
    
    def _load_yaml_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        config_file = None
        
        # Check CLI argument first
        if self.args and self.args.config:
            config_file = self.args.config
        else:
            # Look for default config files in order of preference
            default_locations = [
                './lxd_inventory.yml',
                './lxd_inventory.yaml',
                '~/.config/lxd_inventory.yml',
                '~/.config/lxd_inventory.yaml',
                '/etc/lxd_inventory.yml',
                '/etc/lxd_inventory.yaml'
            ]
            
            for location in default_locations:
                expanded_path = os.path.expanduser(location)
                if os.path.exists(expanded_path) and os.path.isfile(expanded_path):
                    config_file = expanded_path
                    if self.debug:
                        print(f"Debug: Using config file: {config_file}", file=sys.stderr)
                    break
        
        if not config_file:
            if self.debug:
                print("Debug: No config file found, using defaults", file=sys.stderr)
            return {}
        
        try:
            with open(config_file, 'r') as f:
                config_data = yaml.safe_load(f) or {}
                if self.debug:
                    print(f"Debug: Loaded config from {config_file}", file=sys.stderr)
                return config_data
        except FileNotFoundError:
            if self.args and self.args.config:
                print(f"Error: Config file '{config_file}' not found", file=sys.stderr)
                sys.exit(1)
            return {}
        except yaml.YAMLError as e:
            print(f"Error parsing config file '{config_file}': {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error loading config file '{config_file}': {e}", file=sys.stderr)
            sys.exit(1)
    
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
        prefer_ipv6 = self.config['filters']['prefer_ipv6']
        
        if self.debug:
            print(f"Debug: Ignoring interfaces: {ignore_interfaces}", file=sys.stderr)
            print(f"Debug: Prefer IPv6: {prefer_ipv6}", file=sys.stderr)
        
        # Collect all valid IP addresses
        ipv4_addresses = []
        ipv6_addresses = []
        
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
                if not isinstance(addr, dict) or addr.get('scope') != 'global':
                    continue
                    
                ip_address = addr.get('address')
                family = addr.get('family')
                
                if family == 'inet' and ip_address:
                    ipv4_addresses.append((ip_address, interface_name))
                    if self.debug:
                        print(f"Debug: Found IPv4 {ip_address} on interface {interface_name}", file=sys.stderr)
                elif family == 'inet6' and ip_address:
                    ipv6_addresses.append((ip_address, interface_name))
                    if self.debug:
                        print(f"Debug: Found IPv6 {ip_address} on interface {interface_name}", file=sys.stderr)
        
        # Return preferred IP type first, fallback to the other
        if prefer_ipv6:
            if ipv6_addresses:
                ip, interface = ipv6_addresses[0]
                if self.debug:
                    print(f"Debug: Using preferred IPv6 {ip} from interface {interface}", file=sys.stderr)
                return ip
            elif ipv4_addresses:
                ip, interface = ipv4_addresses[0]
                if self.debug:
                    print(f"Debug: Fallback to IPv4 {ip} from interface {interface}", file=sys.stderr)
                return ip
        else:
            if ipv4_addresses:
                ip, interface = ipv4_addresses[0]
                if self.debug:
                    print(f"Debug: Using preferred IPv4 {ip} from interface {interface}", file=sys.stderr)
                return ip
            elif ipv6_addresses:
                ip, interface = ipv6_addresses[0]
                if self.debug:
                    print(f"Debug: Fallback to IPv6 {ip} from interface {interface}", file=sys.stderr)
                return ip
        
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
        """Get variables for a specific instance in Ansible dynamic inventory format."""
        inventory = self._generate_inventory()
        instance_vars = inventory['_meta']['hostvars'].get(instance_name, {})
        
        if not instance_vars:
            return {}
        
        # Return in the same format as --list but for just this instance
        return {
            "_meta": {
                "hostvars": {
                    instance_name: instance_vars
                }
            }
        }
    
    def list_inventory(self) -> str:
        """Return the full inventory as JSON."""
        return json.dumps(self._generate_inventory(), indent=2)


def main():
    parser = argparse.ArgumentParser(description='LXD Ansible Dynamic Inventory')
    parser.add_argument('--list', action='store_true', help='List all hosts')
    parser.add_argument('--instance', help='Get variables for a specific instance (mutually exclusive with --list)')
    parser.add_argument('--yaml', action='store_true', help='Output in YAML format')
    
    # LXD connection
    parser.add_argument('--host', help='LXD host/cluster to connect to (overrides LXD_ENDPOINT)')
    
    # Filtering arguments
    parser.add_argument('--status', choices=['running', 'stopped', 'frozen', 'error'], 
                       help='Filter by instance status (only applies to --list)')
    parser.add_argument('--type', help='Filter by type (vm,lxc) - comma separated (only applies to --list)')
    parser.add_argument('--project', help='Filter by project(s) - comma separated')
    parser.add_argument('--all-projects', action='store_true', 
                       help='Include all projects - overrides --project (only applies to --list)')
    parser.add_argument('--profile', help='Filter by profile(s) - comma separated (only applies to --list)')
    parser.add_argument('--ignore-interface', help='Interfaces to ignore when finding IP (comma separated)')
    parser.add_argument('--prefer-ipv6', action='store_true', 
                       help='Prefer IPv6 addresses over IPv4 for ansible_host')
    parser.add_argument('--config', help='Path to YAML configuration file (default: ./lxd_inventory.yml)')
    parser.add_argument('--debug', action='store_true', 
                       help='Enable debug output')
    
    args = parser.parse_args()
    
    # Validate mutually exclusive arguments
    if args.list and args.instance:
        parser.error("--list and --instance are mutually exclusive")
    
    if not args.list and not args.instance:
        parser.error("Either --list or --instance must be specified")
    
    inventory = LXDInventory(args)
    
    if args.list:
        if args.yaml:
            data = inventory._generate_inventory()
            print(yaml.dump(data, default_flow_style=False))
        else:
            print(inventory.list_inventory())
    elif args.instance:
        instance_vars = inventory.get_instance_vars(args.instance)
        if not instance_vars.get('_meta', {}).get('hostvars'):
            print(f"Instance '{args.instance}' not found", file=sys.stderr)
            sys.exit(1)
        if args.yaml:
            print(yaml.dump(instance_vars, default_flow_style=False))
        else:
            print(json.dumps(instance_vars, indent=2))


if __name__ == '__main__':
    main()