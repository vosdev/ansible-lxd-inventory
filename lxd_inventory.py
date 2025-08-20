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
        self.config = self._load_config()
        self.session = self._create_session()
        
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from environment variables, CLI args, or defaults."""
        config = {
            'endpoint': os.getenv('LXD_ENDPOINT', 'unix:///var/lib/lxd/unix.socket'),
            'cert_path': os.getenv('LXD_CERT_PATH'),
            'key_path': os.getenv('LXD_KEY_PATH'),
            'ca_cert_path': os.getenv('LXD_CA_CERT_PATH'),
            'verify_ssl': os.getenv('LXD_VERIFY_SSL', 'false').lower() == 'true',
        }
        
        # Handle filters with CLI args taking precedence
        filters = {}
        
        # Status filter
        if self.args and self.args.status:
            filters['status'] = [self.args.status]
        else:
            filters['status'] = os.getenv('LXD_FILTER_STATUS', 'running,stopped').split(',')
        
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
                projects_data = self._make_request("/projects")
                projects = list(projects_data.keys())
            except Exception as e:
                print(f"Warning: Could not fetch all projects, using default: {e}", file=sys.stderr)
                projects = ['default']
        
        for project in projects:
            try:
                path = f"/instances?recursion=2&project={project}"
                instances = self._make_request(path)
                # Add project info to each instance
                for instance in instances:
                    instance['lxd_project'] = project
                all_instances.extend(instances)
            except Exception as e:
                print(f"Warning: Could not fetch instances from project '{project}': {e}", file=sys.stderr)
                continue
        
        return all_instances
    
    def _filter_instance(self, instance: Dict[str, Any]) -> bool:
        """Apply filters to determine if an instance should be included."""
        # Filter by status
        status_filter = [s.lower() for s in self.config['filters']['status']]
        if 'all' not in status_filter and instance['status'].lower() not in status_filter:
            return False
        
        # Filter by type
        type_filter = self.config['filters']['type']
        if 'all' not in type_filter and instance['type'] not in type_filter:
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
        network = state.get('network', {})
        
        # Look for the first non-loopback IPv4 address
        for interface_name, interface_data in network.items():
            if interface_name == 'lo':  # Skip loopback
                continue
            
            addresses = interface_data.get('addresses', [])
            for addr in addresses:
                if addr.get('family') == 'inet' and addr.get('scope') == 'global':
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
    
    def get_host_vars(self, hostname: str) -> Dict[str, Any]:
        """Get variables for a specific host."""
        inventory = self._generate_inventory()
        return inventory['_meta']['hostvars'].get(hostname, {})
    
    def list_inventory(self) -> str:
        """Return the full inventory as JSON."""
        return json.dumps(self._generate_inventory(), indent=2)


def main():
    parser = argparse.ArgumentParser(description='LXD Ansible Dynamic Inventory')
    parser.add_argument('--list', action='store_true', help='List all hosts')
    parser.add_argument('--host', help='Get variables for a specific host')
    parser.add_argument('--yaml', action='store_true', help='Output in YAML format')
    
    # Filtering arguments
    parser.add_argument('--status', choices=['running', 'stopped'], 
                       help='Filter by instance status')
    parser.add_argument('--type', help='Filter by type (vm,lxc) - comma separated')
    parser.add_argument('--project', help='Filter by project(s) - comma separated')
    parser.add_argument('--all-projects', action='store_true', 
                       help='Include all projects (overrides --project)')
    parser.add_argument('--profile', help='Filter by profile(s) - comma separated')
    
    args = parser.parse_args()
    
    inventory = LXDInventory(args)
    
    if args.list:
        if args.yaml:
            data = inventory._generate_inventory()
            print(yaml.dump(data, default_flow_style=False))
        else:
            print(inventory.list_inventory())
    elif args.host:
        host_vars = inventory.get_host_vars(args.host)
        if args.yaml:
            print(yaml.dump(host_vars, default_flow_style=False))
        else:
            print(json.dumps(host_vars, indent=2))
    else:
        parser.print_help()


if __name__ == '__main__':
    main()