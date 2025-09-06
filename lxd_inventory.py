#!/usr/bin/env python3
"""
LXD Ansible Dynamic Inventory Script with Multi-Endpoint Support

This script connects to multiple LXD servers and generates Ansible inventory
based on containers and VMs with configurable filtering per endpoint.

Usage:
    python lxd_inventory.py --list
    python lxd_inventory.py --instance <instancename>

Configuration:
    Create a YAML configuration file (lxd_inventory.yml) or specify with --config.
    The script looks for config files in this order:
    1. --config /path/to/config.yml (CLI argument)
    2. Config based on script filename (e.g., lxd_inventory_dev.py -> lxd_inventory_dev.yml)
    3. ./lxd_inventory.yml
    4. ./lxd_inventory.yaml  
    5. ~/.config/lxd_inventory.yml
    6. ~/.config/lxd_inventory.yaml
    7. /etc/lxd_inventory.yml
    8. /etc/lxd_inventory.yaml
    
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
        
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file and CLI args, with defaults."""
        # Load from YAML config file first
        config_data = self._load_yaml_config()
        
        # Check if we're using multi-endpoint format
        if 'lxd_endpoints' in config_data:
            # Multi-endpoint format
            return self._process_multi_endpoint_config(config_data)
        else:
            # No config file or empty - use defaults
            return self._get_default_config()
    
    def _process_multi_endpoint_config(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process multi-endpoint configuration format."""
        config = {
            'global_defaults': config_data.get('global_defaults', {}),
            'endpoints': {}
        }
        
        # Process each endpoint
        for endpoint_name, endpoint_config in config_data.get('lxd_endpoints', {}).items():
            processed_endpoint = self._process_endpoint_config(endpoint_name, endpoint_config, config['global_defaults'])
            config['endpoints'][endpoint_name] = processed_endpoint
        
        # If CLI --endpoint is specified, filter to only those endpoints
        if self.args and self.args.endpoint:
            # Parse comma-separated endpoint names
            requested_endpoints = [name.strip() for name in self.args.endpoint.split(',')]
            available_endpoints = list(config['endpoints'].keys())
            
            # Validate that all requested endpoints exist
            missing_endpoints = []
            for endpoint_name in requested_endpoints:
                if endpoint_name not in config['endpoints']:
                    missing_endpoints.append(endpoint_name)
            
            if missing_endpoints:
                print(f"Error: Endpoint(s) not found in configuration: {', '.join(missing_endpoints)}", file=sys.stderr)
                print(f"Available endpoints: {', '.join(available_endpoints)}", file=sys.stderr)
                sys.exit(1)
            
            # Filter to only the specified endpoints
            filtered_endpoints = {}
            for endpoint_name in requested_endpoints:
                filtered_endpoints[endpoint_name] = config['endpoints'][endpoint_name]
            
            config['endpoints'] = filtered_endpoints
            
            if self.debug:
                print(f"Debug: Filtered to endpoints: {', '.join(requested_endpoints)}", file=sys.stderr)
        
        return config
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration when no config file exists."""
        global_defaults = {
            'verify_ssl': False,
            'filters': {
                'status': ['running', 'stopped', 'frozen', 'error'],
                'type': ['container', 'virtual-machine'],
                'projects': ['all'],
                'profiles': [],
                'ignore_interfaces': ['lo', 'docker0', 'cilium_host', 'cilium_vxlan', 'cilium_net'],
                'prefer_ipv6': False,
                'exclude_names': [],
                'exclude_projects': [],
                'tags': {}
            }
        }
        
        # Determine endpoint from CLI or default
        endpoint = 'unix:///var/lib/lxd/unix.socket'
        if self.args and self.args.endpoint:
            # With new --endpoint behavior, this shouldn't happen since we expect 
            # endpoint names from config, but keep fallback for edge cases
            print("Warning: --endpoint specified but no config file found. Using default endpoint.", file=sys.stderr)
        
        endpoint_config = {'endpoint': endpoint}
        
        return {
            'global_defaults': global_defaults,
            'endpoints': {
                'default': self._process_endpoint_config('default', endpoint_config, global_defaults)
            }
        }
    
    def _process_endpoint_config(self, endpoint_name: str, endpoint_config: Dict[str, Any], global_defaults: Dict[str, Any]) -> Dict[str, Any]:
        """Process configuration for a single endpoint, merging with global defaults and CLI args."""
        config = {
            'name': endpoint_name,
            'endpoint': endpoint_config.get('endpoint', 'unix:///var/lib/lxd/unix.socket'),
            'verify_ssl': endpoint_config.get('verify_ssl', global_defaults.get('verify_ssl', False)),
            'cert_path': endpoint_config.get('cert_path', global_defaults.get('cert_path')),
            'key_path': endpoint_config.get('key_path', global_defaults.get('key_path')),
            'ca_cert_path': endpoint_config.get('ca_cert_path', global_defaults.get('ca_cert_path')),
            'hostname_format': endpoint_config.get('hostname_format', global_defaults.get('hostname_format', '{name}')),
        }
        
        # Merge filters: global defaults < endpoint config < CLI args
        filters = {}
        global_filters = global_defaults.get('filters', {})
        endpoint_filters = endpoint_config.get('filters', {})
        
        # Status filter
        if self.args and self.args.status:
            # Support comma-separated statuses
            cli_statuses = [s.strip().lower() for s in self.args.status.split(',')]
            filters['status'] = cli_statuses
        elif 'status' in endpoint_filters:
            status = endpoint_filters['status']
            filters['status'] = status if isinstance(status, list) else status.split(',')
        elif 'status' in global_filters:
            status = global_filters['status']
            filters['status'] = status if isinstance(status, list) else status.split(',')
        else:
            filters['status'] = ['running', 'stopped', 'frozen', 'error']
        
        # Type filter
        if self.args and self.args.type:
            type_map = {'vm': 'virtual-machine', 'lxc': 'container'}
            cli_types = [t.strip() for t in self.args.type.split(',')]
            filters['type'] = [type_map.get(t, t) for t in cli_types]
        elif 'type' in endpoint_filters:
            type_filter = endpoint_filters['type']
            filters['type'] = type_filter if isinstance(type_filter, list) else type_filter.split(',')
        elif 'type' in global_filters:
            type_filter = global_filters['type']
            filters['type'] = type_filter if isinstance(type_filter, list) else type_filter.split(',')
        else:
            filters['type'] = ['container', 'virtual-machine']
        
        # Project filter
        if self.args and self.args.all_projects:
            filters['projects'] = ['all']
        elif self.args and self.args.project:
            filters['projects'] = [p.strip() for p in self.args.project.split(',')]
        elif 'projects' in endpoint_filters:
            projects = endpoint_filters['projects']
            if projects == 'all' or 'all' in projects:
                filters['projects'] = ['all']
            else:
                filters['projects'] = projects if isinstance(projects, list) else projects.split(',')
        elif 'projects' in global_filters:
            projects = global_filters['projects']
            if projects == 'all' or 'all' in projects:
                filters['projects'] = ['all']
            else:
                filters['projects'] = projects if isinstance(projects, list) else projects.split(',')
        else:
            filters['projects'] = ['default']
        
        # Profile filter
        if self.args and self.args.profile:
            filters['profiles'] = [p.strip() for p in self.args.profile.split(',')]
        elif 'profiles' in endpoint_filters:
            profiles = endpoint_filters['profiles']
            filters['profiles'] = profiles if isinstance(profiles, list) else profiles.split(',')
        elif 'profiles' in global_filters:
            profiles = global_filters['profiles']
            filters['profiles'] = profiles if isinstance(profiles, list) else profiles.split(',')
        else:
            filters['profiles'] = []
        
        # Ignore interfaces filter
        if self.args and self.args.ignore_interface:
            filters['ignore_interfaces'] = [i.strip() for i in self.args.ignore_interface.split(',')]
        elif 'ignore_interfaces' in endpoint_filters:
            ignore = endpoint_filters['ignore_interfaces']
            filters['ignore_interfaces'] = ignore if isinstance(ignore, list) else ignore.split(',')
        elif 'ignore_interfaces' in global_filters:
            ignore = global_filters['ignore_interfaces']
            filters['ignore_interfaces'] = ignore if isinstance(ignore, list) else ignore.split(',')
        else:
            filters['ignore_interfaces'] = ['lo', 'docker0', 'cilium_host', 'cilium_vxlan', 'cilium_net']
        
        # IPv6 preference
        if self.args and self.args.prefer_ipv6:
            filters['prefer_ipv6'] = True
        elif 'prefer_ipv6' in endpoint_filters:
            filters['prefer_ipv6'] = endpoint_filters['prefer_ipv6']
        elif 'prefer_ipv6' in global_filters:
            filters['prefer_ipv6'] = global_filters['prefer_ipv6']
        else:
            filters['prefer_ipv6'] = False
        
        # Exclude names - from config file only
        if 'exclude_names' in endpoint_filters:
            exclude = endpoint_filters['exclude_names']
            filters['exclude_names'] = exclude if isinstance(exclude, list) else exclude.split(',')
        elif 'exclude_names' in global_filters:
            exclude = global_filters['exclude_names']
            filters['exclude_names'] = exclude if isinstance(exclude, list) else exclude.split(',')
        else:
            filters['exclude_names'] = []
        
        # Exclude projects - from config file only
        if 'exclude_projects' in endpoint_filters:
            exclude = endpoint_filters['exclude_projects']
            filters['exclude_projects'] = exclude if isinstance(exclude, list) else exclude.split(',')
        elif 'exclude_projects' in global_filters:
            exclude = global_filters['exclude_projects']
            filters['exclude_projects'] = exclude if isinstance(exclude, list) else exclude.split(',')
        else:
            filters['exclude_projects'] = []
        
        # Tag filters - from CLI and config file
        if self.args and self.args.tag:
            filters['tags'] = self._parse_tag_filters(self.args.tag.split(','))
        elif 'tags' in endpoint_filters:
            tag_filter = endpoint_filters['tags']
            if isinstance(tag_filter, dict):
                filters['tags'] = tag_filter
            elif isinstance(tag_filter, list):
                filters['tags'] = self._parse_tag_filters(tag_filter)
            else:
                filters['tags'] = self._parse_tag_filters([tag_filter])
        elif 'tags' in global_filters:
            tag_filter = global_filters['tags']
            if isinstance(tag_filter, dict):
                filters['tags'] = tag_filter
            elif isinstance(tag_filter, list):
                filters['tags'] = self._parse_tag_filters(tag_filter)
            else:
                filters['tags'] = self._parse_tag_filters([tag_filter])
        else:
            filters['tags'] = {}
        
        config['filters'] = filters
        return config
    
    def _parse_tag_filters(self, tag_list: List[str]) -> Dict[str, str]:
        """Parse tag filter strings into a dictionary.
        
        Supports formats like:
        - 'user.ansible=true'
        - 'user.env=production'
        - 'user.managed!=false' (negation)
        """
        parsed_tags = {}
        
        for tag_filter in tag_list:
            tag_filter = tag_filter.strip()
            if not tag_filter:
                continue
            
            # Handle negation (!=)
            if '!=' in tag_filter:
                key, value = tag_filter.split('!=', 1)
                key = key.strip()
                value = value.strip()
                parsed_tags[key] = {'value': value, 'negate': True}
            elif '=' in tag_filter:
                key, value = tag_filter.split('=', 1)
                key = key.strip()
                value = value.strip()
                parsed_tags[key] = {'value': value, 'negate': False}
            else:
                # Just a key without value means check for existence
                key = tag_filter.strip()
                parsed_tags[key] = {'value': None, 'negate': False}
        
        return parsed_tags
    
    def _get_config_file_from_script_name(self) -> Optional[str]:
        """Determine config file based on script name."""
        script_path = sys.argv[0]
        script_name = os.path.basename(script_path)
        
        # Remove .py extension and look for matching config
        base_name = script_name
        if base_name.endswith('.py'):
            base_name = base_name[:-3]
        
        # List of locations to check, in order of preference
        config_locations = [            
            f'/etc/{base_name}.yml',
            f'/etc/{base_name}.yaml',
            f'~/.config/{base_name}.yml',
            f'~/.config/{base_name}.yaml',
            f'./{base_name}.yml',
            f'./{base_name}.yaml'
        ]
        
        for location in config_locations:
            expanded_path = os.path.expanduser(location)
            if os.path.exists(expanded_path) and os.path.isfile(expanded_path):
                if self.debug:
                    print(f"Debug: Found script-name-based config file: {expanded_path}", file=sys.stderr)
                return expanded_path
        
        return None
    
    def _load_yaml_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        config_file = None
        
        # Check CLI argument first
        if self.args and self.args.config:
            config_file = self.args.config
        else:
            # Check for config file based on script name
            config_file = self._get_config_file_from_script_name()
            
            if not config_file:
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
                            print(f"Debug: Using default config file: {config_file}", file=sys.stderr)
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
    
    def _create_session(self, endpoint_config: Dict[str, Any]) -> requests.Session:
        """Create a requests session with appropriate configuration for an endpoint."""
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
        if endpoint_config['cert_path'] and endpoint_config['key_path']:
            session.cert = (endpoint_config['cert_path'], endpoint_config['key_path'])
        
        if endpoint_config['ca_cert_path']:
            session.verify = endpoint_config['ca_cert_path']
        else:
            session.verify = endpoint_config['verify_ssl']
        
        return session
    
    def _make_request(self, endpoint_config: Dict[str, Any], path: str) -> Dict[str, Any]:
        """Make a request to the LXD API for a specific endpoint."""
        if endpoint_config['endpoint'].startswith('unix://'):
            # Unix socket connection
            try:
                import requests_unixsocket
                session = requests_unixsocket.Session()
                url = endpoint_config['endpoint'].replace('unix://', 'http+unix://') + '/1.0' + path
            except ImportError:
                print(f"Error: requests-unixsocket package is required for Unix socket connections", file=sys.stderr)
                print(f"Install with: pip install requests-unixsocket", file=sys.stderr)
                sys.exit(1)
        else:
            # HTTP/HTTPS connection
            session = self._create_session(endpoint_config)
            url = f"{endpoint_config['endpoint']}/1.0{path}"
        
        try:
            response = session.get(url)
            response.raise_for_status()
            data = response.json()
            
            if data.get('type') == 'error':
                raise Exception(f"LXD API error: {data.get('error', 'Unknown error')}")
            
            return data.get('metadata', {})
        except requests.exceptions.RequestException as e:
            print(f"Error connecting to LXD endpoint '{endpoint_config['name']}' at {endpoint_config['endpoint']}: {e}", file=sys.stderr)
            return {}
        except Exception as e:
            print(f"Error with LXD endpoint '{endpoint_config['name']}': {e}", file=sys.stderr)
            return {}
    
    def _should_exclude_project(self, project_name: str, endpoint_name: str, exclude_projects: List[str]) -> bool:
        """Check if a project should be excluded based on exclude_projects patterns.
        
        Supports multiple formats:
        - 'backup' - excludes backup project
        - 'regex:^test.*' - excludes projects matching regex pattern
        """
        if not exclude_projects:
            return False
        
        for exclude_pattern in exclude_projects:
            exclude_pattern = exclude_pattern.strip()
            if not exclude_pattern:
                continue
            
            # Handle regex patterns
            if exclude_pattern.startswith('regex:'):
                regex_pattern = exclude_pattern[6:]  # Remove 'regex:' prefix
                
                try:
                    import re
                    if re.match(regex_pattern, project_name):
                        if self.debug:
                            print(f"Debug: Project '{project_name}' excluded from endpoint '{endpoint_name}' by regex pattern '{exclude_pattern}'", file=sys.stderr)
                        return True
                except re.error as e:
                    print(f"Warning: Invalid regex pattern '{regex_pattern}' in exclude_projects: {e}", file=sys.stderr)
                    continue
            
            # Handle simple project name matching
            else:
                if exclude_pattern == project_name:
                    if self.debug:
                        print(f"Debug: Project '{project_name}' excluded from endpoint '{endpoint_name}' by pattern '{exclude_pattern}'", file=sys.stderr)
                    return True
        
        return False
    
    def _get_instances(self, endpoint_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get all instances from a specific LXD endpoint with detailed information."""
        all_instances = []
        projects = endpoint_config['filters']['projects']
        endpoint_name = endpoint_config['name']
        exclude_projects = endpoint_config['filters']['exclude_projects']
        
        if self.debug:
            print(f"Debug: Fetching instances from endpoint '{endpoint_name}' at {endpoint_config['endpoint']}", file=sys.stderr)
            if exclude_projects:
                print(f"Debug: Project exclusion filters for endpoint '{endpoint_name}': {exclude_projects}", file=sys.stderr)
        
        if 'all' in projects:
            # Get list of all projects first
            try:
                if self.debug:
                    print(f"Debug: Fetching all projects from endpoint '{endpoint_name}'...", file=sys.stderr)
                    
                projects_data = self._make_request(endpoint_config, "/projects?recursion=0")
                
                if not projects_data:
                    if self.debug:
                        print(f"Debug: No projects data returned from endpoint '{endpoint_name}', skipping", file=sys.stderr)
                    return []
                
                if self.debug:
                    print(f"Debug: Projects API response type: {type(projects_data)}", file=sys.stderr)
                    print(f"Debug: Projects API response: {projects_data[:3] if isinstance(projects_data, list) else projects_data}", file=sys.stderr)
                
                if isinstance(projects_data, list):
                    # Extract project names from URLs like '/1.0/projects/default'
                    projects = [url.split('/')[-1] for url in projects_data if '/projects/' in url]
                else:
                    # If recursion=0 doesn't work, try without recursion parameter
                    try:
                        projects_data = self._make_request(endpoint_config, "/projects")
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
                    print(f"Debug: Found projects in endpoint '{endpoint_name}': {projects}", file=sys.stderr)
                    
            except Exception as e:
                print(f"Warning: Could not fetch all projects from endpoint '{endpoint_name}', using default: {e}", file=sys.stderr)
                projects = ['default']
        
        # Filter out excluded projects
        if exclude_projects:
            original_projects = projects[:]
            projects = [p for p in projects if not self._should_exclude_project(p, endpoint_name, exclude_projects)]
            
            excluded = set(original_projects) - set(projects)
            if excluded and self.debug:
                print(f"Debug: Excluded projects from endpoint '{endpoint_name}': {sorted(excluded)}", file=sys.stderr)
            
            if not projects:
                if self.debug:
                    print(f"Debug: All projects excluded from endpoint '{endpoint_name}', skipping", file=sys.stderr)
                return []
        
        for project in projects:
            try:
                if self.debug:
                    print(f"Debug: Fetching instances from project '{project}' in endpoint '{endpoint_name}'...", file=sys.stderr)
                    
                path = f"/instances?recursion=2&project={project}"
                instances = self._make_request(endpoint_config, path)
                
                if not instances:
                    if self.debug:
                        print(f"Debug: No instances returned from project '{project}' in endpoint '{endpoint_name}'", file=sys.stderr)
                    continue
                
                if self.debug:
                    print(f"Debug: Found {len(instances)} instances in project '{project}' from endpoint '{endpoint_name}'", file=sys.stderr)
                
                # Add project and endpoint info to each instance
                for instance in instances:
                    instance['lxd_project'] = project
                    instance['lxd_endpoint'] = endpoint_name
                all_instances.extend(instances)
            except Exception as e:
                print(f"Warning: Could not fetch instances from project '{project}' in endpoint '{endpoint_name}': {e}", file=sys.stderr)
                continue
        
        if self.debug:
            print(f"Debug: Total instances found in endpoint '{endpoint_name}': {len(all_instances)}", file=sys.stderr)
        
        return all_instances
    
    def _filter_instance(self, instance: Dict[str, Any], endpoint_config: Dict[str, Any]) -> bool:
        """Apply filters to determine if an instance should be included."""
        filters = endpoint_config['filters']
        
        # Filter by status
        status_filter = [s.lower().strip() for s in filters['status']]
        if status_filter and 'all' not in status_filter:
            if instance['status'].lower() not in status_filter:
                return False
        
        # Filter by type
        type_filter = filters['type']
        if type_filter and 'all' not in type_filter and instance['type'] not in type_filter:
            return False
        
        # Filter by profiles
        profile_filter = filters['profiles']
        if profile_filter and not any(profile in instance.get('profiles', []) for profile in profile_filter):
            return False
        
        # Check exclude_names filter
        exclude_names = filters.get('exclude_names', [])
        if self._should_exclude_instance(instance, exclude_names):
            return False
        
        # Filter by tags (user.* configuration keys)
        tag_filters = filters.get('tags', {})
        if tag_filters:
            if not self._match_tag_filters(instance, tag_filters):
                return False
        
        return True
    
    def _should_exclude_instance(self, instance: Dict[str, Any], exclude_names: List[str]) -> bool:
        """Check if an instance should be excluded based on exclude_names patterns.
        
        Supports multiple formats:
        - 'vm1' - excludes vm1 from any project
        - 'project/vm1' - excludes vm1 only from specified project
        - 'regex:^test.*' - excludes instances matching regex pattern
        - 'regex:project/^test.*' - excludes instances matching regex in specific project
        """
        if not exclude_names:
            return False
        
        instance_name = instance['name']
        instance_project = instance.get('lxd_project', 'default')
        
        for exclude_pattern in exclude_names:
            exclude_pattern = exclude_pattern.strip()
            if not exclude_pattern:
                continue
            
            # Handle regex patterns
            if exclude_pattern.startswith('regex:'):
                regex_pattern = exclude_pattern[6:]  # Remove 'regex:' prefix
                
                # Check for project-specific regex: 'regex:project/pattern'
                if '/' in regex_pattern:
                    pattern_project, pattern = regex_pattern.split('/', 1)
                    if pattern_project != instance_project:
                        continue
                else:
                    # Global regex pattern (any project)
                    pattern = regex_pattern
                
                try:
                    import re
                    if re.match(pattern, instance_name):
                        if self.debug:
                            print(f"Debug: Instance {instance_name} excluded by regex pattern '{exclude_pattern}'", file=sys.stderr)
                        return True
                except re.error as e:
                    print(f"Warning: Invalid regex pattern '{pattern}' in exclude_names: {e}", file=sys.stderr)
                    continue
            
            # Handle project/name format
            elif '/' in exclude_pattern:
                pattern_project, pattern_name = exclude_pattern.split('/', 1)
                if pattern_project == instance_project and pattern_name == instance_name:
                    if self.debug:
                        print(f"Debug: Instance {instance_name} excluded by project-specific pattern '{exclude_pattern}'", file=sys.stderr)
                    return True
            
            # Handle simple name matching (any project)
            else:
                if exclude_pattern == instance_name:
                    if self.debug:
                        print(f"Debug: Instance {instance_name} excluded by global name pattern '{exclude_pattern}'", file=sys.stderr)
                    return True
        
        return False
    
    def _match_tag_filters(self, instance: Dict[str, Any], tag_filters: Dict[str, Any]) -> bool:
        """Check if an instance matches the tag filters."""
        # Check both config (instance-specific) and expanded_config (includes profile values)
        instance_config = instance.get('config', {})
        expanded_config = instance.get('expanded_config', {})
        
        for tag_key, filter_spec in tag_filters.items():
            # Handle different formats for tag filters
            if isinstance(filter_spec, dict):
                # Dictionary format (from _parse_tag_filters or explicit YAML dict)
                expected_value = filter_spec['value']
                negate = filter_spec['negate']
                actual_tag_key = tag_key
            else:
                # String value from YAML config - check for negation syntax
                expected_value = filter_spec
                if tag_key.endswith('!='):
                    # Handle negation syntax in YAML: "user.ansible!=": "false"
                    actual_tag_key = tag_key[:-2]  # Remove the != suffix
                    negate = True
                else:
                    actual_tag_key = tag_key
                    negate = False
            
            # Check expanded_config first (includes profile values), then fall back to instance config
            actual_value = expanded_config.get(actual_tag_key)
            if actual_value is None:
                actual_value = instance_config.get(actual_tag_key)
            
            config_source = "expanded" if actual_tag_key in expanded_config else "instance"
            
            if expected_value is None:
                # Just checking for key existence
                key_exists = (actual_tag_key in expanded_config) or (actual_tag_key in instance_config)
                if negate:
                    if key_exists:
                        if self.debug:
                            print(f"Debug: Instance {instance['name']} excluded - tag '{actual_tag_key}' exists in {config_source} config (negated)", file=sys.stderr)
                        return False
                else:
                    if not key_exists:
                        if self.debug:
                            print(f"Debug: Instance {instance['name']} excluded - tag '{actual_tag_key}' missing from both configs", file=sys.stderr)
                        return False
            else:
                # Checking for specific value
                if negate:
                    if actual_value == expected_value:
                        if self.debug:
                            print(f"Debug: Instance {instance['name']} excluded - tag '{actual_tag_key}={actual_value}' in {config_source} config matches negated value '{expected_value}'", file=sys.stderr)
                        return False
                else:
                    if actual_value != expected_value:
                        if self.debug:
                            print(f"Debug: Instance {instance['name']} excluded - tag '{actual_tag_key}={actual_value}' in {config_source} config doesn't match required value '{expected_value}'", file=sys.stderr)
                        return False
        
        return True
    
    def _get_instance_ips(self, instance: Dict[str, Any], endpoint_config: Dict[str, Any]) -> tuple[Optional[str], List[str]]:
        """Extract all IP addresses from an instance and return (primary_ip, all_ips_list)."""
        state = instance.get('state', {})
        if not state:
            return None, []
            
        network = state.get('network')
        if not network or not isinstance(network, dict):
            return None, []
        
        filters = endpoint_config['filters']
        ignore_interfaces = filters['ignore_interfaces']
        prefer_ipv6 = filters['prefer_ipv6']
        
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
        
        # Determine primary IP based on preference
        primary_ip = None
        if prefer_ipv6:
            if ipv6_addresses:
                primary_ip, interface = ipv6_addresses[0]
                if self.debug:
                    print(f"Debug: Using preferred IPv6 {primary_ip} from interface {interface}", file=sys.stderr)
            elif ipv4_addresses:
                primary_ip, interface = ipv4_addresses[0]
                if self.debug:
                    print(f"Debug: Fallback to IPv4 {primary_ip} from interface {interface}", file=sys.stderr)
        else:
            if ipv4_addresses:
                primary_ip, interface = ipv4_addresses[0]
                if self.debug:
                    print(f"Debug: Using preferred IPv4 {primary_ip} from interface {interface}", file=sys.stderr)
            elif ipv6_addresses:
                primary_ip, interface = ipv6_addresses[0]
                if self.debug:
                    print(f"Debug: Fallback to IPv6 {primary_ip} from interface {interface}", file=sys.stderr)
        
        # Create ordered list of all IPs based on preference
        all_ips = []
        if prefer_ipv6:
            # IPv6 first, then IPv4
            all_ips.extend([ip for ip, _ in ipv6_addresses])
            all_ips.extend([ip for ip, _ in ipv4_addresses])
        else:
            # IPv4 first, then IPv6
            all_ips.extend([ip for ip, _ in ipv4_addresses])
            all_ips.extend([ip for ip, _ in ipv6_addresses])
        
        if self.debug and all_ips:
            print(f"Debug: All IPs ordered by preference: {all_ips}", file=sys.stderr)
        
        return primary_ip, all_ips
    
    def _format_hostname(self, instance: Dict[str, Any], endpoint_config: Dict[str, Any]) -> str:
        """Format hostname using the configured hostname_format template."""
        format_template = endpoint_config['hostname_format']
        
        # Available variables for hostname formatting
        variables = {
            'name': instance['name'],
            'project': instance.get('lxd_project', 'default'),
            'endpoint': instance.get('lxd_endpoint', endpoint_config['name']),
            'type': instance['type'],
            'status': instance['status'].lower(),
        }
        
        # Replace variables in the format template
        try:
            hostname = format_template.format(**variables)
            # Sanitize hostname - replace invalid characters with hyphens
            import re
            hostname = re.sub(r'[^a-zA-Z0-9.-]', '-', hostname)
            # Remove consecutive hyphens and leading/trailing hyphens
            hostname = re.sub(r'-+', '-', hostname).strip('-')
            return hostname
        except KeyError as e:
            print(f"Warning: Invalid variable '{e.args[0]}' in hostname_format template, using instance name", file=sys.stderr)
            return instance['name']
        except Exception as e:
            print(f"Warning: Error formatting hostname: {e}, using instance name", file=sys.stderr)
            return instance['name']
    
    def _generate_inventory(self) -> Dict[str, Any]:
        """Generate the Ansible inventory from all endpoints."""
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
        
        # Process each endpoint
        for endpoint_name, endpoint_config in self.config['endpoints'].items():
            if self.debug:
                print(f"Debug: Processing endpoint '{endpoint_name}'", file=sys.stderr)
            
            instances = self._get_instances(endpoint_config)
            
            # Create endpoint-specific group
            endpoint_group_name = f'lxd_endpoint_{endpoint_name}'
            groups[endpoint_group_name] = {'hosts': []}
            
            for instance in instances:
                if not self._filter_instance(instance, endpoint_config):
                    continue
                
                name = instance['name']
                
                # Format hostname using the configured template
                formatted_hostname = self._format_hostname(instance, endpoint_config)
                
                # Check for hostname conflicts and resolve them
                original_hostname = formatted_hostname
                counter = 1
                while formatted_hostname in inventory['_meta']['hostvars']:
                    formatted_hostname = f"{original_hostname}-{counter}"
                    counter += 1
                    if counter > 100:  # Prevent infinite loop
                        print(f"Warning: Too many hostname conflicts for {original_hostname}, using {formatted_hostname}", file=sys.stderr)
                        break
                
                primary_ip, all_ips = self._get_instance_ips(instance, endpoint_config)
                
                # Add to main groups
                groups['all']['hosts'].append(formatted_hostname)
                groups[endpoint_group_name]['hosts'].append(formatted_hostname)
                
                # Add to type-specific groups
                if instance['type'] == 'container':
                    groups['lxd_containers']['hosts'].append(formatted_hostname)
                elif instance['type'] == 'virtual-machine':
                    groups['lxd_vms']['hosts'].append(formatted_hostname)
                
                # Add to status-specific groups
                status = instance['status'].lower()
                if status == 'running':
                    groups['lxd_running']['hosts'].append(formatted_hostname)
                elif status == 'stopped':
                    groups['lxd_stopped']['hosts'].append(formatted_hostname)
                elif status == 'frozen':
                    groups['lxd_frozen']['hosts'].append(formatted_hostname)
                elif status == 'error':
                    groups['lxd_error']['hosts'].append(formatted_hostname)
                
                # Create profile-based groups
                for profile in instance.get('profiles', []):
                    group_name = f'lxd_profile_{profile}'
                    if group_name not in groups:
                        groups[group_name] = {'hosts': []}
                    groups[group_name]['hosts'].append(formatted_hostname)
                
                # Create project-based groups
                project = instance.get('lxd_project', 'default')
                project_group_name = f'lxd_project_{project}'
                if project_group_name not in groups:
                    groups[project_group_name] = {'hosts': []}
                groups[project_group_name]['hosts'].append(formatted_hostname)
                
                # Add host variables
                hostvars = {
                    'lxd_name': name,
                    'lxd_hostname': formatted_hostname,
                    'lxd_type': instance['type'],
                    'lxd_status': instance['status'],
                    'lxd_architecture': instance['architecture'],
                    'lxd_profiles': instance.get('profiles', []),
                    'lxd_project': instance.get('lxd_project', 'default'),
                    'lxd_endpoint': instance.get('lxd_endpoint', endpoint_name),
                    'lxd_endpoint_url': endpoint_config['endpoint'],
                }
                
                # Add IP addresses
                if primary_ip:
                    hostvars['ansible_host'] = primary_ip
                
                if all_ips:
                    hostvars['lxd_ip'] = all_ips
                elif primary_ip:
                    # Fallback for backward compatibility if only primary IP exists
                    hostvars['lxd_ip'] = [primary_ip]
                
                # Add configuration details
                config = instance.get('config', {})
                if config:
                    hostvars['lxd_config'] = config
                
                # Add expanded config (e.g., image info)
                expanded_config = instance.get('expanded_config', {})
                if expanded_config:
                    hostvars['lxd_expanded_config'] = expanded_config
                
                inventory['_meta']['hostvars'][formatted_hostname] = hostvars
        
        # Remove empty groups
        groups = {k: v for k, v in groups.items() if v['hosts']}
        inventory.update(groups)
        
        return inventory
    
    def get_instance_vars(self, instance_name: str) -> Dict[str, Any]:
        """Get variables for a specific instance in Ansible dynamic inventory format."""
        inventory = self._generate_inventory()
        
        # Look for exact hostname match first
        if instance_name in inventory['_meta']['hostvars']:
            instance_vars = inventory['_meta']['hostvars'][instance_name]
            return {
                "_meta": {
                    "hostvars": {
                        instance_name: instance_vars
                    }
                }
            }
        
        # Look for matches by original LXD name
        matching_hosts = {}
        for host_name, host_vars in inventory['_meta']['hostvars'].items():
            if host_vars.get('lxd_name') == instance_name:
                matching_hosts[host_name] = host_vars
        
        if len(matching_hosts) == 1:
            # Single match found - return it with the formatted hostname
            host_name, host_vars = next(iter(matching_hosts.items()))
            return {
                "_meta": {
                    "hostvars": {
                        host_name: host_vars
                    }
                }
            }
        elif len(matching_hosts) > 1:
            # Multiple matches - return all with formatted hostnames
            print(f"Warning: Multiple instances found with name '{instance_name}':", file=sys.stderr)
            for host_name, host_vars in matching_hosts.items():
                print(f"  - {host_name} (project: {host_vars.get('lxd_project', 'default')}, endpoint: {host_vars.get('lxd_endpoint', 'unknown')})", file=sys.stderr)
            return {
                "_meta": {
                    "hostvars": matching_hosts
                }
            }
        
        # No matches found
        return {}
    
    def list_inventory(self) -> str:
        """Return the full inventory as JSON."""
        return json.dumps(self._generate_inventory(), indent=2)


def main():
    parser = argparse.ArgumentParser(description='LXD Ansible Dynamic Inventory with Multi-Endpoint Support')
    
    # Make --list and --instance mutually exclusive but not required
    action_group = parser.add_mutually_exclusive_group(required=False)
    action_group.add_argument('--list', action='store_true', help='List all hosts (default behavior)')
    action_group.add_argument('--instance', help='Get variables for a specific instance')
    
    parser.add_argument('--yaml', action='store_true', help='Output in YAML format')
    
    # LXD connection
    parser.add_argument('--endpoint', help='Filter to specific endpoint(s) from config file - comma separated (e.g., production,development)')
    
    # Filtering arguments (these apply to all endpoints when using multi-endpoint config)
    parser.add_argument('--status', 
                       help='Filter by instance status - comma separated (e.g. running,stopped) (applies to all endpoints)')
    parser.add_argument('--type', help='Filter by type (vm,lxc) - comma separated (applies to all endpoints)')
    parser.add_argument('--project', help='Filter by project(s) - comma separated (applies to all endpoints)')
    parser.add_argument('--all-projects', action='store_true', 
                       help='Include all projects - overrides --project (applies to all endpoints)')
    parser.add_argument('--profile', help='Filter by profile(s) - comma separated (applies to all endpoints)')
    parser.add_argument('--tag', help='Filter by user.* tags - comma separated (e.g. user.ansible=true,user.env!=test) (applies to all endpoints)')
    parser.add_argument('--ignore-interface', help='Interfaces to ignore when finding IP (comma separated, applies to all endpoints)')
    parser.add_argument('--prefer-ipv6', action='store_true', 
                       help='Prefer IPv6 addresses over IPv4 for ansible_host (applies to all endpoints)')
    parser.add_argument('--config', help='Path to YAML configuration file (default: script-name-based or ./lxd_inventory.yml)')
    parser.add_argument('--debug', action='store_true', 
                       help='Enable debug output')
    
    args = parser.parse_args()
    
    # If no action specified, default to --list behavior (critical for Ansible compatibility)
    if not args.list and not args.instance:
        args.list = True
    
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