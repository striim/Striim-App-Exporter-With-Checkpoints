#!/usr/bin/env python3
"""
Striim Export All with Checkpoint Position Updater

This script:
1. Authenticates with Striim API
2. Optionally drops types matching a namespace and source component name prefix
3. Gets list of all applications using 'mon;'
4. Exports all applications using EXPORT APPLICATION ALL with passphrase
5. Gets checkpoint history for all applications
6. Updates TQL files with checkpoint positions (only for Global.MysqlReader and Global.MSSqlReader sources)

Usage:
    python striim_export_all_with_checkpoint.py
    python striim_export_all_with_checkpoint.py --environment production
    python striim_export_all_with_checkpoint.py --passphrase "custom123"
    python striim_export_all_with_checkpoint.py --droptypes admin MySource
"""

import requests
import json
import argparse
import re
import sys
import os
import zipfile
import tempfile
from typing import Dict, Optional, List, Tuple, List
from pathlib import Path
import config


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
            response = requests.post(auth_url, data=data, headers=headers)
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
            response = requests.post(api_url, headers=headers, data=command)
            response.raise_for_status()
            
            result = response.json()
            return result
            
        except requests.exceptions.RequestException as e:
            print(f"✗ Failed to execute command '{command}': {e}")
            return None
        except json.JSONDecodeError as e:
            print(f"✗ Failed to parse response for command '{command}': {e}")
            return None
    
    def list_types(self) -> List[str]:
        """Get list of all types using 'list types;' command"""
        if not self.token:
            print("✗ Not authenticated. Call authenticate() first.")
            return []

        result = self.execute_command("list types;")
        if not result:
            return []

        try:
            # Parse the list types command output
            if len(result) > 0 and 'output' in result[0]:
                output = result[0]['output']

                # The API returns a list of type objects in this format:
                # [{"type1": {"name": "Global.MonitorBatchEvent"}}, {"type2": {"name": "Global.DataBlockEvent"}}, ...]
                if isinstance(output, list):
                    types = []
                    for item in output:
                        if isinstance(item, dict):
                            # Each item has a key like "type1", "type2", etc. with a dict containing "name"
                            for type_key, type_info in item.items():
                                if isinstance(type_info, dict) and 'name' in type_info:
                                    types.append(type_info['name'])
                    return types

            print("✗ No types found in list types output")
            return []

        except Exception as e:
            print(f"✗ Error parsing types list: {e}")
            return []

    def drop_types_by_prefix(self, namespace: str, source_component_name: str) -> bool:
        """Drop all types matching the given namespace and source component name prefix"""
        if not self.token:
            print("✗ Not authenticated. Call authenticate() first.")
            return False

        # Construct prefix pattern
        prefix = f"{namespace}.{source_component_name}_"
        print(f"Looking for types with prefix: {prefix}")

        # Get all types
        all_types = self.list_types()
        if not all_types:
            print("✗ No types found or failed to list types")
            return False

        print(f"Found {len(all_types)} total types")

        # Filter types by prefix
        matching_types = [t for t in all_types if t.startswith(prefix)]

        if not matching_types:
            print(f"No types found with prefix '{prefix}'")
            return True  # Not an error, just nothing to drop

        print(f"Types matching prefix '{prefix}':")
        for type_name in matching_types:
            print(f"  {type_name}")
        print()

        # Drop each matching type
        dropped_count = 0
        failed_count = 0

        for type_name in matching_types:
            print(f"Dropping type: {type_name}")
            command = f"drop type {type_name};"
            result = self.execute_command(command)

            if result:
                dropped_count += 1
                print(f"  ✓ Successfully dropped {type_name}")
            else:
                failed_count += 1
                print(f"  ✗ Failed to drop {type_name}")

        print(f"\nDrop types summary:")
        print(f"  Total matching types: {len(matching_types)}")
        print(f"  Successfully dropped: {dropped_count}")
        print(f"  Failed to drop: {failed_count}")

        return failed_count == 0

    def export_all_applications(self, export_path: str, passphrase: str = None) -> bool:
        """Export all applications to a zip file using EXPORT APPLICATION ALL"""
        if not self.token:
            print("✗ Not authenticated. Call authenticate() first.")
            return False

        # Use config default if passphrase not provided
        if passphrase is None:
            passphrase = config.get_config()['passphrase']

        api_url = f"{self.base_url}/api/v2/tungsten"
        headers = {
            'authorization': f'STRIIM-TOKEN {self.token}',
            'content-type': 'text/plain'
        }

        command = f'EXPORT APPLICATION ALL passphrase="{passphrase}";'

        try:
            response = requests.post(api_url, headers=headers, data=command)
            response.raise_for_status()

            # The response should be the zip file content
            with open(export_path, 'wb') as f:
                f.write(response.content)

            return True

        except requests.exceptions.RequestException as e:
            print(f"✗ Failed to export all applications: {e}")
            return False
        except Exception as e:
            print(f"✗ Failed to write export file {export_path}: {e}")
            return False


def get_application_list(api: StriimAPI) -> List[str]:
    """Get list of all applications from 'mon;' command"""
    print("Getting list of applications...")
    
    result = api.execute_command("mon;")
    if not result:
        return []
    
    try:
        # Parse the mon command output
        if len(result) > 0 and 'output' in result[0]:
            output = result[0]['output']
            if 'striimApplications' in output:
                apps = output['striimApplications']
                app_names = [app['fullName'] for app in apps if app.get('fullName')]
                print(f"✓ Found {len(app_names)} applications: {', '.join(app_names)}")
                return app_names
        
        print("✗ No applications found in mon output")
        return []
        
    except Exception as e:
        print(f"✗ Error parsing application list: {e}")
        return []


def extract_applications_from_zip(zip_path: str, stage_dir: str, app_names: List[str], overwrite: bool = True) -> Dict[str, str]:
    """Extract individual TQL files from the exported zip file"""
    print(f"\nExtracting applications from {zip_path} to {stage_dir}/...")

    # Create stage directory if it doesn't exist
    Path(stage_dir).mkdir(exist_ok=True)

    extracted_files = {}

    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # List all files in the zip
            zip_files = zip_ref.namelist()
            print(f"Found {len(zip_files)} files in zip archive")

            for app_name in app_names:
                # Look for TQL file matching this app name
                # Files in zip are named like: admin.SQLIL_1759437185439.tql (keeping the dots)
                tql_filename = None

                for zip_file in zip_files:
                    if zip_file.endswith('.tql') and zip_file.startswith(app_name + '_'):
                        tql_filename = zip_file
                        break

                if not tql_filename:
                    print(f"  ⚠️  No TQL file found for {app_name} (looking for pattern: {app_name}_*.tql)")
                    continue

                # Define output path
                output_file = os.path.join(stage_dir, f"{app_name}.tql")

                # Check if file exists and handle accordingly
                if os.path.exists(output_file) and not overwrite:
                    print(f"  ⏭️  Skipping {app_name} -> {output_file} (file exists)")
                    extracted_files[app_name] = output_file  # Still include in processing
                    continue

                action = "Overwriting" if os.path.exists(output_file) else "Extracting"
                print(f"  {action} {app_name} -> {output_file}")

                # Extract and save the file
                with zip_ref.open(tql_filename) as source, open(output_file, 'wb') as target:
                    target.write(source.read())

                extracted_files[app_name] = output_file
                print(f"  ✓ Successfully extracted {app_name}")

        print(f"✓ Successfully extracted {len(extracted_files)} out of {len(app_names)} applications")
        return extracted_files

    except Exception as e:
        print(f"✗ Error extracting applications from zip: {e}")
        return {}


def get_checkpoint_history(api: StriimAPI, app_name: str) -> Optional[Dict]:
    """Get checkpoint history for an application and extract position info"""
    command = f"SHOW {app_name} CHECKPOINT HISTORY;"
    result = api.execute_command(command)

    if not result:
        return None

    try:
        # Get the first checkpoint (most recent)
        if not result or len(result) == 0:
            return None

        first_result = result[0]
        if 'output' not in first_result or len(first_result['output']) == 0:
            return None

        first_checkpoint = first_result['output'][0]
        position_summary = first_checkpoint.get('sourcePositionSummary', '')

        # Try to extract MySQL binlog position first
        binlog_match = re.search(r'BinlogName\s*:\s*([^\s\n]+)', position_summary)
        position_match = re.search(r'BinLogPosition\s*:\s*(\d+)', position_summary)

        if binlog_match and position_match:
            binlog_name = binlog_match.group(1)
            position = position_match.group(1)
            return {
                'type': 'mysql',
                'binlog_name': binlog_name,
                'position': position,
                'format_string': f"FileName:{binlog_name};offset:{position}"
            }

        # Try to extract SQL Server LSN (CommitScn)
        lsn_match = re.search(r'CommitScn:\s*([A-Fa-f0-9]+)', position_summary)
        if lsn_match:
            lsn = lsn_match.group(1)
            return {
                'type': 'sqlserver',
                'lsn': lsn,
                'format_string': f"LSN:0x{lsn}"
            }

        # Try to extract MongoDB UTC DateTime
        mongodb_match = re.search(r'UTC DateTime value = ([^]]+)', position_summary)
        if mongodb_match:
            datetime_str = mongodb_match.group(1)
            # Remove .000Z suffix to get format like 2025-10-02T20:48:28
            clean_datetime = datetime_str.replace('.000Z', '')
            return {
                'type': 'mongodb',
                'datetime': clean_datetime,
                'format_string': clean_datetime
            }

        # Try to extract Oracle SCN (System Change Number)
        # Oracle checkpoint format shows: {OpenSCN[30507229]-CommitSCN[30507230]-SeqNum[2]}
        oracle_scn_match = re.search(r'CommitSCN\[(\d+)\]', position_summary)
        if oracle_scn_match:
            scn = oracle_scn_match.group(1)
            return {
                'type': 'oracle',
                'scn': scn,
                'format_string': scn
            }

        return None

    except Exception as e:
        print(f"  ✗ Error extracting position info for {app_name}: {e}")
        return None


def get_reader_type(tql_file_path: str) -> Optional[str]:
    """Check what type of reader the TQL file uses"""
    try:
        with open(tql_file_path, 'r') as f:
            content = f.read()

        # Look for CREATE SOURCE ... USING Global.MysqlReader pattern
        if re.search(r'CREATE\s+(?:OR\s+REPLACE\s+)?SOURCE\s+\w+\s+USING\s+Global\.MysqlReader', content, re.IGNORECASE):
            return 'mysql'

        # Look for CREATE SOURCE ... USING Global.MSSqlReader pattern
        if re.search(r'CREATE\s+(?:OR\s+REPLACE\s+)?SOURCE\s+\w+\s+USING\s+Global\.MSSqlReader', content, re.IGNORECASE):
            return 'sqlserver'

        # Look for CREATE SOURCE ... USING Global.MSJet pattern
        if re.search(r'CREATE\s+(?:OR\s+REPLACE\s+)?SOURCE\s+\w+\s+USING\s+Global\.MSJet', content, re.IGNORECASE):
            return 'sqlserver'  # Treat MSJet the same as MSSqlReader

        # Look for CREATE SOURCE ... USING Global.MongoDBReader pattern
        if re.search(r'CREATE\s+(?:OR\s+REPLACE\s+)?SOURCE\s+\w+\s+USING\s+Global\.MongoDBReader', content, re.IGNORECASE):
            return 'mongodb'

        # Look for CREATE SOURCE ... USING Global.OracleReader pattern
        if re.search(r'CREATE\s+(?:OR\s+REPLACE\s+)?SOURCE\s+\w+\s+USING\s+Global\.OracleReader', content, re.IGNORECASE):
            return 'oracle'

        # Look for CREATE SOURCE ... USING Global.OJet pattern
        if re.search(r'CREATE\s+(?:OR\s+REPLACE\s+)?SOURCE\s+\w+\s+USING\s+Global\.OJet', content, re.IGNORECASE):
            return 'oracle'  # Treat OJet the same as Oracle

        return None

    except Exception as e:
        print(f"  ✗ Error reading TQL file {tql_file_path}: {e}")
        return None


def update_tql_with_position(tql_file_path: str, reader_type: str, position_info: Dict) -> bool:
    """Update TQL file with checkpoint position"""
    try:
        # Read the original file
        with open(tql_file_path, 'r') as f:
            content = f.read()

        position_string = position_info['format_string']
        updated_content = content

        if reader_type == 'mysql':
            # Replace StartTimestamp: 'NOW' with the position information
            pattern = r"StartTimestamp:\s*'NOW'"
            replacement = f"StartTimestamp: '{position_string}'"
            updated_content = re.sub(pattern, replacement, content)

        elif reader_type == 'sqlserver':
            # Replace StartPosition: 'NOW' with the LSN information
            pattern = r"StartPosition:\s*'NOW'"
            replacement = f"StartPosition: '{position_string}'"
            updated_content = re.sub(pattern, replacement, content)

        elif reader_type == 'mongodb':
            # For MongoDB, check if startTimestamp already exists
            pattern = r"startTimestamp:\s*'[^']*'"
            if re.search(pattern, content):
                # Replace existing startTimestamp
                replacement = f"startTimestamp: '{position_string}'"
                updated_content = re.sub(pattern, replacement, content)
            else:
                # Add startTimestamp field before the closing ) of the source definition
                # Find the source definition and add the field
                source_pattern = r'(CREATE\s+SOURCE\s+\w+\s+USING\s+Global\.MongoDBReader\s*\([^)]+)(\s*\)\s*OUTPUT)'
                match = re.search(source_pattern, content, re.IGNORECASE | re.DOTALL)
                if match:
                    before_closing = match.group(1)
                    after_closing = match.group(2)
                    # Add startTimestamp before the closing parenthesis
                    replacement = f"{before_closing}, \n  startTimestamp: '{position_string}'{after_closing}"
                    updated_content = re.sub(source_pattern, replacement, content, flags=re.IGNORECASE | re.DOTALL)

        elif reader_type == 'oracle':
            # For Oracle and OJet, check if startSCN already exists
            pattern = r"startSCN:\s*'[^']*'"
            if re.search(pattern, content):
                # Replace existing startSCN
                replacement = f"startSCN: '{position_string}'"
                updated_content = re.sub(pattern, replacement, content)
            else:
                # Add startSCN field before the closing ) of the source definition
                # Find the source definition and add the field (handles both OracleReader and OJet)
                source_pattern = r'(CREATE\s+(?:OR\s+REPLACE\s+)?SOURCE\s+\w+\s+USING\s+Global\.(?:OracleReader|OJet)\s*\([^)]+)(\s*\)\s*OUTPUT)'
                match = re.search(source_pattern, content, re.IGNORECASE | re.DOTALL)
                if match:
                    before_closing = match.group(1)
                    after_closing = match.group(2)
                    # Add startSCN before the closing parenthesis
                    replacement = f"{before_closing}, \n  startSCN: '{position_string}'{after_closing}"
                    updated_content = re.sub(source_pattern, replacement, content, flags=re.IGNORECASE | re.DOTALL)

        # Check if replacement was made
        if updated_content == content:
            return False  # No replacement made

        # Write the updated content back to the same file
        with open(tql_file_path, 'w') as f:
            f.write(updated_content)

        return True

    except Exception as e:
        print(f"  ✗ Error updating TQL file {tql_file_path}: {e}")
        return False


def main():
    # Load default configuration
    default_config = config.get_config()
    default_processing = config.get_processing_config()

    parser = argparse.ArgumentParser(description='Export all Striim applications and update TQL files with checkpoint positions')
    parser.add_argument('--stage-dir', default=default_processing['stage_directory'],
                       help=f'Directory to export applications to (default: {default_processing["stage_directory"]})')
    parser.add_argument('--url', default=default_config['url'],
                       help=f'Striim server URL (default: {default_config["url"]})')
    parser.add_argument('--username', default=default_config['username'],
                       help=f'Striim username (default: {default_config["username"]})')
    parser.add_argument('--password', default=default_config['password'],
                       help='Striim password (default: from config)')
    parser.add_argument('--no-overwrite', action='store_true',
                       help='Skip export if TQL file already exists')
    parser.add_argument('--passphrase', default=default_config['passphrase'],
                       help=f'Passphrase for export encryption (default: {default_config["passphrase"]})')
    parser.add_argument('--environment', choices=['default'] + list(config.ENVIRONMENTS.keys()),
                       default='default', help='Environment configuration to use')
    parser.add_argument('--droptypes', nargs=2, metavar=('NAMESPACE', 'SOURCE_COMPONENT_NAME'),
                       help='Drop all types matching the given namespace and source component name prefix (format: namespace.source_component_name_*)')

    args = parser.parse_args()

    # If environment is specified, override with environment-specific config
    if args.environment != 'default':
        env_config = config.get_config(args.environment)
        # Only override if not explicitly provided via command line
        if args.url == default_config['url']:
            args.url = env_config['url']
        if args.username == default_config['username']:
            args.username = env_config['username']
        if args.password == default_config['password']:
            args.password = env_config['password']
        if args.passphrase == default_config['passphrase']:
            args.passphrase = env_config['passphrase']
    
    print("🚀 Starting Striim Export All with Checkpoint Position Updater")
    print(f"   Striim URL: {args.url}")
    print(f"   Stage Directory: {args.stage_dir}")
    if args.droptypes:
        namespace, source_component_name = args.droptypes
        print(f"   Drop Types Prefix: {namespace}.{source_component_name}_")
    print()
    
    # Initialize API client
    api = StriimAPI(args.url, args.username, args.password)
    
    # Step 1: Authenticate
    print("Step 1: Authenticating with Striim...")
    if not api.authenticate():
        sys.exit(1)
    print()

    # Optional: Drop types if requested
    if args.droptypes:
        namespace, source_component_name = args.droptypes
        print(f"Step 1.5: Dropping types with prefix {namespace}.{source_component_name}_...")
        if not api.drop_types_by_prefix(namespace, source_component_name):
            print("⚠️  Some types failed to drop, but continuing with export...")
        print()

    # Step 2: Get application list
    print("Step 2: Getting application list...")
    app_names = get_application_list(api)
    if not app_names:
        print("✗ No applications found. Exiting.")
        sys.exit(1)
    print()
    
    # Step 3: Export all applications using bulk export
    print("Step 3: Exporting all applications...")

    # Create stage directory if it doesn't exist
    Path(args.stage_dir).mkdir(exist_ok=True)

    # Define zip file path
    zip_path = os.path.join(args.stage_dir, "all_applications.zip")

    # Export all applications to zip file
    print(f"Exporting all applications to {zip_path} with passphrase '{args.passphrase}'...")
    if not api.export_all_applications(zip_path, args.passphrase):
        print("✗ Failed to export applications. Exiting.")
        sys.exit(1)

    print(f"✓ Successfully exported all applications to {zip_path}")

    # Extract individual TQL files from zip
    overwrite = not args.no_overwrite
    exported_files = extract_applications_from_zip(zip_path, args.stage_dir, app_names, overwrite)
    if not exported_files:
        print("✗ No applications extracted successfully. Exiting.")
        sys.exit(1)
    print()
    
    # Step 4: Process each exported application
    print("Step 4: Processing applications for checkpoint updates...")

    updated_count = 0
    mysql_reader_count = 0
    sqlserver_reader_count = 0
    mongodb_reader_count = 0
    oracle_reader_count = 0
    checkpoint_data_count = 0

    for app_name, tql_file in exported_files.items():
        print(f"\n  Processing {app_name}...")

        # Check what type of reader it uses
        reader_type = get_reader_type(tql_file)
        if not reader_type:
            print(f"    ⏭️  Skipping - does not use Global.MysqlReader, Global.MSSqlReader, Global.MSJet, Global.MongoDBReader, Global.OracleReader, or Global.OJet")
            continue

        if reader_type == 'mysql':
            mysql_reader_count += 1
            print(f"    ✓ Uses Global.MysqlReader")
        elif reader_type == 'sqlserver':
            sqlserver_reader_count += 1
            print(f"    ✓ Uses Global.MSSqlReader")
        elif reader_type == 'mongodb':
            mongodb_reader_count += 1
            print(f"    ✓ Uses Global.MongoDBReader")
        elif reader_type == 'oracle':
            oracle_reader_count += 1
            print(f"    ✓ Uses Global.OracleReader")

        # Get checkpoint history
        position_info = get_checkpoint_history(api, app_name)
        if not position_info:
            print(f"    ⏭️  Skipping - no checkpoint data available")
            continue

        checkpoint_data_count += 1
        print(f"    ✓ Found checkpoint: {position_info['format_string']}")

        # Update TQL file
        if update_tql_with_position(tql_file, reader_type, position_info):
            updated_count += 1
            print(f"    ✓ Updated TQL file with position: {position_info['format_string']}")
        else:
            if reader_type == 'mysql':
                start_field = "StartTimestamp"
            elif reader_type == 'sqlserver':
                start_field = "StartPosition"
            else:  # mongodb
                start_field = "startTimestamp"
            print(f"    ⚠️  No {start_field}: 'NOW' found to update")

    print(f"\n🎉 Processing complete!")
    print(f"   Total applications: {len(app_names)}")
    print(f"   Exported successfully: {len(exported_files)}")
    print(f"   Using Global.MysqlReader: {mysql_reader_count}")
    print(f"   Using Global.MSSqlReader: {sqlserver_reader_count}")
    print(f"   Using Global.MongoDBReader: {mongodb_reader_count}")
    print(f"   Using Global.OracleReader: {oracle_reader_count}")
    print(f"   With checkpoint data: {checkpoint_data_count}")
    print(f"   Updated with positions: {updated_count}")


if __name__ == "__main__":
    main()
