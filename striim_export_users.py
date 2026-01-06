#!/usr/bin/env python3
"""
Striim User Export Script

This script:
1. Authenticates with Striim API
2. Gets list of all users using 'list users;'
3. Describes each user to get their roles
4. Generates CREATE USER statements (excluding system users: admin, sys)
5. Optionally exports custom roles with --include-roles flag
6. Writes statements to users/users.tql

Usage:
    python striim_export_users.py
    python striim_export_users.py --environment production
    python striim_export_users.py --include-roles
"""

import requests
import json
import argparse
import sys
import os
from typing import Dict, Optional, List
from pathlib import Path
import config
import urllib3

# Disable SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class StriimAPI:
    def __init__(self, base_url: str = None, username: str = None, password: str = None):
        # Load default config if parameters not provided
        default_config = config.get_config()

        self.base_url = base_url or default_config['url']
        self.username = username or default_config['username']
        self.password = password or default_config['password']
        self.token = None
        
    def authenticate(self) -> bool:
        """Authenticate with Striim and get token"""
        auth_url = f"{self.base_url}/security/authenticate"
        data = f"username={self.username}&password={self.password}"
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        
        try:
            response = requests.post(auth_url, data=data, headers=headers, verify=False)
            response.raise_for_status()
            
            result = response.json()
            self.token = result.get("token")
            
            if self.token:
                print(f"✓ Authentication successful. Token: {self.token}")
                return True
            else:
                print("✗ Authentication failed: No token received")
                return False
                
        except requests.exceptions.RequestException as e:
            print(f"✗ Authentication failed: {e}")
            return False
        except json.JSONDecodeError as e:
            print(f"✗ Authentication failed: Invalid JSON response - {e}")
            return False
    
    def execute_command(self, command: str) -> Optional[Dict]:
        """Generic function to execute any TQL command via API"""
        if not self.token:
            print("✗ Not authenticated. Call authenticate() first.")
            return None
            
        api_url = f"{self.base_url}/api/v2/tungsten"
        headers = {
            'authorization': f'STRIIM-TOKEN {self.token}',
            'content-type': 'text/plain'
        }
        
        try:
            response = requests.post(api_url, headers=headers, data=command, verify=False)
            response.raise_for_status()
            
            result = response.json()
            return result
            
        except requests.exceptions.RequestException as e:
            print(f"✗ Failed to execute command '{command}': {e}")
            return None
        except json.JSONDecodeError as e:
            print(f"✗ Failed to parse response for command '{command}': {e}")
            return None


def export_users(api: StriimAPI, include_roles: bool = False) -> bool:
    """Get list of all users from 'list users;' command, describe each user, and write CREATE USER statements to file.

    If include_roles is True, also exports custom roles at the end of the file.
    """
    print("Getting list of users...")

    result = api.execute_command("list users;")
    if not result:
        return False

    try:
        # Parse the list users command output
        # Expected format: [{"user1": "admin"}, {"user2": "sys"}, ...]
        usernames = []
        if len(result) > 0 and 'output' in result[0]:
            output = result[0]['output']

            # The API returns a list of user objects in format:
            # [{"user1": "admin"}, {"user2": "sys"}, ...]
            if isinstance(output, list):
                for item in output:
                    if isinstance(item, dict):
                        # Look for keys like "user1", "user2", etc.
                        for key, value in item.items():
                            if key.startswith('user') and isinstance(value, str):
                                usernames.append(value)

        if not usernames:
            print("✗ No users found in list users output")
            return False

        print(f"✓ Found {len(usernames)} users: {', '.join(usernames)}")

        # Now describe each user
        user_details = []
        for username in usernames:
            print(f"  Describing user: {username}")
            describe_result = api.execute_command(f"describe user {username};")
            if describe_result:
                user_details.append({
                    'username': username,
                    'details': describe_result
                })
                print(f"    ✓ Retrieved details for {username}")
            else:
                print(f"    ⚠️  Failed to retrieve details for {username}")
                user_details.append({
                    'username': username,
                    'details': None
                })

        print("✓ User list retrieved successfully")

        # Create users directory
        users_dir = "users"
        Path(users_dir).mkdir(exist_ok=True)
        print(f"✓ Created directory: {users_dir}/")

        # Generate CREATE USER statements
        create_statements = []

        # System users to skip
        system_users = ['admin', 'sys']

        for user in user_details:
            username = user.get('username')
            details = user.get('details')

            # Skip system users
            if username in system_users:
                continue

            if not details or len(details) == 0:
                continue

            # Extract roles from the details
            try:
                output = details[0].get('output', [])
                if len(output) > 0:
                    user_info = output[0]
                    roles = user_info.get('roles', [])

                    if roles:
                        # Extract role names, excluding roles that start with the username
                        role_names = [role.get('name') for role in roles
                                     if role.get('name') and not role.get('name').startswith(f"{username}.")]

                        if role_names:
                            # Construct the CREATE USER statement
                            roles_str = ', '.join(role_names)
                            create_stmt = f"CREATE USER {username} IDENTIFIED BY password DEFAULT ROLE {roles_str};"
                            create_statements.append(create_stmt)
            except Exception as e:
                # Log error but don't add to file
                print(f"    ⚠️  Error processing {username}: {e}")

        # Export roles if requested (to separate file)
        roles_tql_file = os.path.join(users_dir, "roles.tql")
        has_roles = False
        if include_roles:
            print("\nExporting custom roles...")
            role_statements = export_roles(api, usernames)
            if role_statements is None:
                print("⚠️  Warning: Failed to export roles, continuing with users only")
                role_statements = ""

            if role_statements:
                try:
                    with open(roles_tql_file, 'w') as f:
                        f.write("-- Striim Custom Roles Export\n")
                        f.write(f"-- Generated on: {Path(roles_tql_file).absolute()}\n")
                        f.write("-- \n")
                        f.write("-- Run this file BEFORE users.tql to create roles that users depend on\n")
                        f.write("-- \n\n")
                        f.write(role_statements)
                        f.write('\n')
                    has_roles = True
                    print(f"✓ Successfully wrote custom roles to: {roles_tql_file}")
                except Exception as e:
                    print(f"⚠️  Warning: Failed to write roles.tql file: {e}")

        # Write CREATE USER statements to file
        users_tql_file = os.path.join(users_dir, "users.tql")
        try:
            with open(users_tql_file, 'w') as f:
                f.write("-- Striim User Export\n")
                f.write(f"-- Generated on: {Path(users_tql_file).absolute()}\n")
                f.write("-- \n")
                f.write("-- Note: Replace 'password' with actual passwords before importing\n")

                # Add reference to roles.tql if roles were exported
                if has_roles:
                    f.write("-- \n")
                    f.write("-- IMPORTANT: Run roles.tql first to create custom roles before running this file\n")
                    f.write("-- @include roles.tql\n")

                f.write("-- \n\n")
                f.write('\n'.join(create_statements))
                f.write('\n')

            print(f"✓ Successfully wrote {len(create_statements)} CREATE USER statements to: {users_tql_file}")
            return True
        except Exception as e:
            print(f"✗ Failed to write users.tql file: {e}")
            return False

    except Exception as e:
        print(f"✗ Error processing user list: {e}")
        return False


def get_usernames(api: StriimAPI) -> List[str]:
    """Get list of all usernames from 'list users;' command"""
    result = api.execute_command("list users;")
    if not result:
        return []

    usernames = []
    try:
        if len(result) > 0 and 'output' in result[0]:
            output = result[0]['output']
            if isinstance(output, list):
                for item in output:
                    if isinstance(item, dict):
                        for key, value in item.items():
                            if key.startswith('user') and isinstance(value, str):
                                usernames.append(value)
    except Exception:
        pass
    return usernames


def export_roles(api: StriimAPI, usernames: List[str]) -> Optional[str]:
    """
    Export custom roles, excluding:
    - Global.* roles
    - System$* roles
    - User-specific roles (user.dev, user.enduser, user.admin, user.useradmin)

    Returns the TQL statements as a string, or None on failure.
    """
    print("Getting list of roles...")

    result = api.execute_command("list roles;")
    if not result:
        return None

    try:
        # Parse the list roles command output
        role_names = []
        if len(result) > 0 and 'output' in result[0]:
            output = result[0]['output']
            if isinstance(output, list):
                for item in output:
                    if isinstance(item, dict):
                        for key, value in item.items():
                            if key.startswith('role') and isinstance(value, str):
                                role_names.append(value)

        if not role_names:
            print("✗ No roles found in list roles output")
            return None

        print(f"✓ Found {len(role_names)} total roles")

        # Build set of user-specific role suffixes to exclude
        user_role_suffixes = ['.dev', '.enduser', '.admin', '.useradmin']

        # Filter out roles to exclude
        roles_to_export = []
        for role_name in role_names:
            # Skip Global.* roles
            if role_name.startswith('Global.'):
                continue

            # Skip System$* roles
            if role_name.startswith('System$'):
                continue

            # Skip user-specific auto-created roles
            is_user_role = False
            for username in usernames:
                for suffix in user_role_suffixes:
                    if role_name == f"{username}{suffix}":
                        is_user_role = True
                        break
                if is_user_role:
                    break

            if not is_user_role:
                roles_to_export.append(role_name)

        print(f"✓ {len(roles_to_export)} custom roles to export (after filtering)")

        if not roles_to_export:
            return ""

        # Describe each role to get permissions
        role_statements = []
        for role_name in roles_to_export:
            print(f"  Describing role: {role_name}")
            describe_result = api.execute_command(f"describe role {role_name};")

            if not describe_result or len(describe_result) == 0:
                print(f"    ⚠️  Failed to retrieve details for {role_name}")
                continue

            try:
                output = describe_result[0].get('output', [])
                if len(output) > 0:
                    role_info = output[0]
                    permissions = role_info.get('permissions', [])

                    # Generate CREATE ROLE statement
                    role_statements.append(f"CREATE ROLE {role_name};")

                    # Generate GRANT statements for each permission
                    for permission in permissions:
                        # Permission format: "GRANT UPDATE ON cluster Global.somepart"
                        # Convert to: GRANT UPDATE ON cluster Global.somepart TO role_name;
                        grant_stmt = f"{permission} TO {role_name};"
                        role_statements.append(grant_stmt)

                    role_statements.append("")  # Empty line between roles
                    print(f"    ✓ Retrieved {len(permissions)} permissions for {role_name}")
            except Exception as e:
                print(f"    ⚠️  Error processing {role_name}: {e}")

        return '\n'.join(role_statements)

    except Exception as e:
        print(f"✗ Error processing role list: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description='Export Striim users to TQL file')
    parser.add_argument('--environment', type=str, default='default',
                       help='Environment name from config.py (default: "default")')
    parser.add_argument('--include-roles', action='store_true',
                       help='Also export custom roles (excluding Global.*, System$*, and user-specific roles)')

    args = parser.parse_args()

    # Get configuration for the specified environment
    env_config = config.get_config(args.environment)

    print("🚀 Starting Striim User Export")
    print(f"   Striim URL: {env_config['url']}")
    if args.include_roles:
        print("   Include Roles: Yes")
    print()

    # Step 1: Authenticate
    print("Step 1: Authenticating with Striim...")
    api = StriimAPI(
        base_url=env_config['url'],
        username=env_config['username'],
        password=env_config['password']
    )

    if not api.authenticate():
        print("✗ Authentication failed. Exiting.")
        sys.exit(1)
    print()

    # Step 2: Export users (and optionally roles)
    step_desc = "Exporting users and roles..." if args.include_roles else "Exporting users..."
    print(f"Step 2: {step_desc}")
    success = export_users(api, include_roles=args.include_roles)
    if not success:
        print("✗ Failed to export users")
        sys.exit(1)

    print("\n🎉 User export complete!")
    sys.exit(0)


if __name__ == "__main__":
    main()

