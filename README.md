# LXD Ansible Dynamic Inventory Script

A powerful and flexible Ansible dynamic inventory script for LXD that supports multiple endpoints, advanced filtering, and hostname customization.

This script was created after running into too many limitations of [community.general.lxd inventory](https://docs.ansible.com/ansible/latest/collections/community/general/lxd_inventory.html). Using many projects on multiple clusters, I ended up with over 20 inventory files. This has now been reduced to 1.

Should also work fine with Incus!

Feel free to request additional features.

## Features

- **Multi-Endpoint Support**: Connect to multiple LXD servers in a single inventory run
- **Tag-based Filtering**: Filter instances using `user.*` configuration keys
- **Flexible Hostname Formatting**: Customize how hostnames appear in your inventory (`{name}.{project}.{endpoint}.example.com`)
- **Project and Profile Filtering**: Filter by LXD projects and profiles
- **Network Interface Control**: Configure IP address selection with interface filtering and IPv4/6 preference
- **Status and Type Filtering**: Include/exclude based on instance status and type
- **Regex-based Name Filtering**: Exclude instances based on names with support for regex
- **SSL/TLS Support**: Full certificate management for secure connections
- **Unix Socket Support**: Local LXD daemon connections
- **Debug Mode**: Detailed logging for troubleshooting

## Installation

### Requirements

```bash
pip install PyYAML requests urllib3
```

For Unix socket connections (local LXD):
```bash
pip install requests-unixsocket
```

### Download

```bash
git clone https://github.com/vosdev/ansible-lxd-inventory
chmod +x ansible-lxd-inventory/lxd_inventory.py
```

## Quick Start

### 1. Basic Configuration

Create `lxd_inventory.yml`:

```yaml
global_defaults:
  verify_ssl: false
  filters:
    status: [running, stopped]
    type: [container, virtual-machine]
    projects: [default]

lxd_endpoints:
  local:
    endpoint: "unix:///var/lib/lxd/unix.socket"
  
  remote:
    endpoint: "https://lxd.example.com:8443"
    cert_path: "/path/to/client.crt"
    key_path: "/path/to/client.key"
```

### 2. Test the Inventory

```bash
# List all instances
./lxd_inventory.py --list

# Get specific instance details
./lxd_inventory.py --instance mycontainer

# Debug mode
./lxd_inventory.py --list --debug
```

### 3. Use with Ansible

```bash
# Run playbook using dynamic inventory
ansible-playbook -i lxd_inventory.py playbook.yml

# List hosts in inventory
ansible-inventory -i lxd_inventory.py --list
```

## Configuration

### Configuration File Locations

The script searches for configuration files in this order:

1. `--config /path/to/config.yml` (CLI argument)
2. `./lxd_inventory.yml`
3. `./lxd_inventory.yaml`
4. `~/.config/lxd_inventory.yml`
5. `~/.config/lxd_inventory.yaml`
6. `/etc/lxd_inventory.yml`
7. `/etc/lxd_inventory.yaml`

### Configuration Structure

```yaml
global_defaults:
  # SSL/TLS settings
  verify_ssl: false
  cert_path: "/path/to/client.crt"
  key_path: "/path/to/client.key"
  ca_cert_path: "/path/to/ca.crt"
  
  # Hostname formatting template
  hostname_format: "{name}"
  
  # Default filters
  filters:
    status: [running, stopped, frozen, error]
    type: [container, virtual-machine]
    projects: [default]
    profiles: []
    tags: {}
    ignore_interfaces: [lo, docker0, lxdbr0]
    prefer_ipv6: false
    exclude_names: []

lxd_endpoints:
  endpoint_name:
    endpoint: "https://lxd.example.com:8443"
    # Override any global_defaults here
    filters:
      # Override specific filters for this endpoint
```

## Filtering

### Status Filtering

```bash
# CLI
./lxd_inventory.py --list --status running
./lxd_inventory.py --list --status running,stopped

# Config
filters:
  status: [running, stopped]
```

### Type Filtering

```bash
# CLI
./lxd_inventory.py --list --type vm
./lxd_inventory.py --list --type vm,lxc

# Config  
filters:
  type: [container, virtual-machine]
```

### Project Filtering

```bash
# CLI - specific projects
./lxd_inventory.py --list --project production,development

# CLI - all projects
./lxd_inventory.py --list --all-projects

# Config
filters:
  projects: [production, development]
  # or
  projects: all
```

### Tag Filtering

Filter instances based on `user.*` configuration keys. Tags can be set directly on instances or inherited from profiles.

#### CLI Examples

```bash
# Only include instances with user.ansible=true
./lxd_inventory.py --list --tag "user.ansible=true"

# Multiple requirements
./lxd_inventory.py --list --tag "user.ansible=true,user.env=production"

# Exclude instances (negation)
./lxd_inventory.py --list --tag "user.ansible!=false"

# Check for key existence
./lxd_inventory.py --list --tag "user.managed"
```

#### Configuration Examples

```yaml
filters:
  tags:
    # Include only instances with user.ansible=true
    user.ansible: "true"
    
    # Exclude instances with user.ansible=false
    "user.ansible!=": "false"
    
    # Multiple requirements
    user.environment: "production"
    "user.backup!=": "true"
```

#### Setting Tags on Instances and Profiles

```bash
# Direct instance configuration
lxc config set myinstance user.ansible true
lxc config set myinstance user.environment production

# Profile-based tags (applies to all instances using the profile)
lxc profile create ansible-managed
lxc profile set ansible-managed user.ansible true
lxc profile add myinstance ansible-managed
```

### Profile Filtering

```bash
# CLI
./lxd_inventory.py --list --profile web,database

# Config
filters:
  profiles: [web, database]
```

### Instance name filtering
```yaml
filters:
  exclude_names:
    - 'vm1'                     # excludes vm1 from any project
    - 'project/vm1'             # excludes vm1 only from specified project
    - 'regex:^vm.*'             # excludes instances matching regex pattern
    - 'regex:project/^vm[1-3]'  # excludes instances matching regex in specific project
```

## Hostname Formatting

Customize how instance names appear in your Ansible inventory using template variables:

### Available Variables

- `{name}` - Instance name
- `{project}` - LXD project name  
- `{endpoint}` - Endpoint name from config
- `{type}` - Instance type (container/virtual-machine)
- `{status}` - Instance status (running/stopped/etc)

### Examples

```yaml
# Global default
global_defaults:
  hostname_format: "{name}"  # Output: "myinstance"

# Include project
global_defaults:
  hostname_format: "{name}.{project}"  # Output: "myinstance.web"

# FQDN-style (My personal preference)
global_defaults:
  hostname_format: "{name}.{project}.{endpoint}.example.com"
  # Output: "myinstance.web.prod.example.com"

# Per-endpoint override
lxd_endpoints:
  production:
    hostname_format: "{name}.prod.example.com"
  development:
    hostname_format: "{name}.dev.example.com"
```

## Network Configuration

### IP Address Selection

```yaml
filters:
  # Interfaces to ignore when finding IP addresses
  ignore_interfaces: [lo, docker0, cilium_host, lxdbr0]
  
  # Prefer IPv6 over IPv4
  prefer_ipv6: true
```

```bash
# CLI
./lxd_inventory.py --list --ignore-interface "lo,docker0" --prefer-ipv6
```

## Multi-Endpoint Usage

### Select Specific Endpoints

```bash
# Single endpoint
./lxd_inventory.py --list --endpoint production

# Multiple endpoints  
./lxd_inventory.py --list --endpoint production,development

# All endpoints (default)
./lxd_inventory.py --list
```

### Endpoint-Specific Configuration

```yaml
lxd_endpoints:
  production:
    endpoint: "https://prod-lxd:8443"
    verify_ssl: true
    filters:
      projects: [production]
      tags:
        user.ansible: "true"
        user.environment: "production"
  
  development:
    endpoint: "https://dev-lxd:8443" 
    verify_ssl: false
    filters:
      projects: [development, testing]
      tags:
        "user.ansible!=": "false"
```

## Inventory Output

### Ansible Groups

The script automatically creates these groups:

- `all` - All instances
- `lxd_containers` - Container instances
- `lxd_vms` - Virtual machine instances  
- `lxd_running` - Running instances
- `lxd_stopped` - Stopped instances
- `lxd_frozen` - Frozen instances
- `lxd_error` - Error state instances
- `lxd_endpoint_<name>` - Instances from specific endpoint
- `lxd_project_<name>` - Instances from specific project
- `lxd_profile_<name>` - Instances with specific profile

### Host Variables

Each instance includes these variables:

```yaml
lxd_name: "original-instance-name"
lxd_hostname: "formatted-hostname"  
lxd_type: "container"
lxd_status: "running"
lxd_architecture: "x86_64"
lxd_profiles: ["default", "web"]
lxd_project: "production"
lxd_endpoint: "production" 
lxd_endpoint_url: "https://prod-lxd:8443"
lxd_ip: "10.0.1.100"
lxd_config: {...}
lxd_expanded_config: {...}
ansible_host: "10.0.1.100"
```

## Advanced Examples

### Production Setup with Tag-based Management

```yaml
global_defaults:
  verify_ssl: true
  cert_path: "/etc/ssl/lxd-client.crt"
  key_path: "/etc/ssl/lxd-client.key"
  hostname_format: "{name}.{project}.{endpoint}.example.com"
  
  filters:
    # Only include ansible-managed instances
    tags:
      user.ansible: "true"
      "user.maintenance!=": "true"

lxd_endpoints:
  production:
    endpoint: "https://prod-lxd.example.com:8443"
    filters:
      projects: [production]
      tags:
        user.environment: "production"
        
  staging:
    endpoint: "https://staging-lxd.example.com:8443" 
    filters:
      projects: [staging]
      tags:
        user.environment: "staging"
```

### Development Environment

```yaml
lxd_endpoints:
  local:
    endpoint: "unix:///var/lib/lxd/unix.socket"
    hostname_format: "{name}.local"
    filters:
      projects: all
      status: [running]
      tags:
        "user.ansible!=": "false"
```

### Multi-Environment with Tag Exclusions

```yaml
lxd_endpoints:
  multi_env:
    endpoint: "https://multi-lxd.example.com:8443"
    filters:
      projects: all
      tags:
        user.managed: "true"           # Must be managed
        "user.backup!=": "true"        # Exclude backup instances
        "user.template!=": "true"      # Exclude templates
        "user.ansible!=": "false"     # Exclude explicitly disabled
```

## Troubleshooting

### Debug Mode

```bash
./lxd_inventory.py --list --debug
```

This shows:
- Configuration file loading
- Endpoint connections
- Instance filtering decisions  
- IP address selection
- Tag matching results

### Common Issues

**No instances found:**
- Check endpoint connectivity with `--debug`
- Verify project names and permissions
- Check tag filters - they may be too restrictive

**SSL Certificate errors:**
- Use `verify_ssl: false` for testing
- Check certificate paths and permissions
- Ensure certificates are valid and not expired

**Permission denied:**
- Verify LXD client certificates have proper permissions
- Check that user is in `lxd` group for Unix socket access

**IP addresses not detected:**
- Check `ignore_interfaces` configuration
- Verify instances have network interfaces configured
- Use `--debug` to see network interface processing

### Testing Configuration

```bash
# Test specific endpoint
./lxd_inventory.py --list --endpoint production --debug

# Test tag filtering
./lxd_inventory.py --list --tag "user.ansible=true" --debug

# Validate instance details  
./lxd_inventory.py --instance mycontainer
```

## Contributing

Bug reports and feature requests are welcome. When reporting issues, please include:

- Configuration file (redacted)
- Command used
- Debug output (`--debug`)
- LXD version and OS details

## License

This script is provided as-is for managing LXD infrastructure with Ansible.