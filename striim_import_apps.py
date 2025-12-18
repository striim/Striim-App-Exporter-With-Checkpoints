#!/usr/bin/env python3
"""
Striim Application Importer

This script:
1. Authenticates with Striim API
2. Scans the import/ directory for TQL files
3. Imports each TQL file to the Striim server
4. Optionally deploys the imported applications

Usage:
    python striim_import_apps.py
    python striim_import_apps.py --environment production
    python striim_import_apps.py --deploy
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


class StriimImportAPI:
    def __init__(self, base_url: str = None, username: str = None, password: str = None, passphrase: str = None):
        # Load default config if parameters not provided
        default_config = config.get_import_config()
        
        self.base_url = base_url or default_config['url']
        self.username = username or default_config['username']
        self.password = password or default_config['password']
        self.passphrase = passphrase or default_config['passphrase']
        self.token = None

    def authenticate(self) -> bool:
        """Authenticate with Striim and get token"""
        url = f"{self.base_url}/security/authenticate"
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        data = f"username={self.username}&password={self.password}"
        
        try:
            response = requests.post(url, headers=headers, data=data, verify=False)
            if response.status_code == 200:
                result = response.json()
                self.token = result.get('token')
                return True
            else:
                print(f"✗ Authentication failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"✗ Authentication error: {e}")
            return False

    def import_tql(self, tql_content: str) -> Dict:
        """Import a TQL file to Striim"""
        if not self.token:
            print("✗ Not authenticated. Call authenticate() first.")
            return None

        # Add passphrase to URL as query parameter
        url = f"{self.base_url}/api/v2/tungsten?passphrase={self.passphrase}"
        headers = {
            'authorization': f'STRIIM-TOKEN {self.token}',
            'content-type': 'text/plain'
        }
        
        try:
            response = requests.post(url, headers=headers, data=tql_content, verify=False)
            if response.status_code == 200:
                return response.json()
            else:
                print(f"✗ Import failed: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            print(f"✗ Import error: {e}")
            return None

    def deploy_application(self, app_name: str) -> bool:
        """Deploy an application"""
        if not self.token:
            print("✗ Not authenticated. Call authenticate() first.")
            return False

        command = f"DEPLOY APPLICATION {app_name};"
        url = f"{self.base_url}/api/v2/tungsten"
        headers = {
            'authorization': f'STRIIM-TOKEN {self.token}',
            'content-type': 'text/plain'
        }
        
        try:
            response = requests.post(url, headers=headers, data=command, verify=False)
            if response.status_code == 200:
                result = response.json()
                if result and len(result) > 0:
                    if result[0].get('executionStatus') == 'Success':
                        return True
                    else:
                        print(f"✗ Deploy failed: {result[0].get('failureMessage', 'Unknown error')}")
                        return False
                return False
            else:
                print(f"✗ Deploy request failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"✗ Deploy error: {e}")
            return False

    def stop_application(self, app_name: str) -> bool:
        """Stop an application"""
        if not self.token:
            print("✗ Not authenticated. Call authenticate() first.")
            return False

        command = f"STOP APPLICATION {app_name};"
        url = f"{self.base_url}/api/v2/tungsten"
        headers = {
            'authorization': f'STRIIM-TOKEN {self.token}',
            'content-type': 'text/plain'
        }

        try:
            response = requests.post(url, headers=headers, data=command, verify=False)
            if response.status_code == 200:
                result = response.json()
                if result and len(result) > 0:
                    if result[0].get('executionStatus') == 'Success':
                        return True
                    else:
                        # It's okay if stop fails (app might not be running)
                        return True
                return True
            else:
                return True  # Continue even if stop fails
        except Exception as e:
            return True  # Continue even if stop fails

    def undeploy_application(self, app_name: str) -> bool:
        """Undeploy an application"""
        if not self.token:
            print("✗ Not authenticated. Call authenticate() first.")
            return False

        command = f"UNDEPLOY APPLICATION {app_name};"
        url = f"{self.base_url}/api/v2/tungsten"
        headers = {
            'authorization': f'STRIIM-TOKEN {self.token}',
            'content-type': 'text/plain'
        }

        try:
            response = requests.post(url, headers=headers, data=command, verify=False)
            if response.status_code == 200:
                result = response.json()
                if result and len(result) > 0:
                    if result[0].get('executionStatus') == 'Success':
                        return True
                    else:
                        # It's okay if undeploy fails (app might not be deployed)
                        return True
                return True
            else:
                return True  # Continue even if undeploy fails
        except Exception as e:
            return True  # Continue even if undeploy fails

    def drop_application(self, app_name: str) -> bool:
        """Drop an application"""
        if not self.token:
            print("✗ Not authenticated. Call authenticate() first.")
            return False

        command = f"DROP APPLICATION {app_name} CASCADE;"
        url = f"{self.base_url}/api/v2/tungsten"
        headers = {
            'authorization': f'STRIIM-TOKEN {self.token}',
            'content-type': 'text/plain'
        }

        try:
            response = requests.post(url, headers=headers, data=command, verify=False)
            if response.status_code == 200:
                result = response.json()
                if result and len(result) > 0:
                    if result[0].get('executionStatus') == 'Success':
                        return True
                    else:
                        # It's okay if drop fails (app might not exist)
                        return True
                return True
            else:
                return True  # Continue even if drop fails
        except Exception as e:
            return True  # Continue even if drop fails


def get_tql_files(import_dir: str) -> List[str]:
    """Get list of TQL files in the import directory"""
    import_path = Path(import_dir)
    
    if not import_path.exists():
        print(f"✗ Import directory does not exist: {import_dir}")
        return []
    
    if not import_path.is_dir():
        print(f"✗ Import path is not a directory: {import_dir}")
        return []
    
    # Get all .tql files, sorted alphabetically
    tql_files = sorted(import_path.glob('*.tql'))
    return [str(f) for f in tql_files]


def extract_app_name(tql_content: str) -> Optional[str]:
    """Extract application name from TQL content"""
    import re

    # Look for CREATE APPLICATION <name> pattern
    match = re.search(r'CREATE\s+APPLICATION\s+(\w+)', tql_content, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def get_full_app_name(filename: str, app_name: str) -> str:
    """Get full qualified application name (namespace.appname) from filename"""
    # Extract namespace from filename (e.g., admin.mgo.tql -> admin.mgo)
    base_name = Path(filename).stem  # Remove .tql extension
    parts = base_name.split('.', 1)

    if len(parts) == 2:
        # Filename has namespace (e.g., admin.mgo)
        return base_name
    else:
        # No namespace in filename, just return app name
        return app_name


def main():
    # Load default configuration
    default_config = config.get_import_config()
    default_processing = config.get_processing_config()
    
    parser = argparse.ArgumentParser(description='Import Striim applications from TQL files')
    parser.add_argument('--import-dir', default=default_processing['import_directory'],
                       help=f'Directory containing TQL files to import (default: {default_processing["import_directory"]})')
    parser.add_argument('--url', default=default_config['url'],
                       help=f'Striim server URL (default: {default_config["url"]})')
    parser.add_argument('--username', default=default_config['username'],
                       help=f'Striim username (default: {default_config["username"]})')
    parser.add_argument('--password', default=default_config['password'],
                       help='Striim password (default: from config)')
    parser.add_argument('--passphrase', default=default_config['passphrase'],
                       help=f'Passphrase for import (default: {default_config["passphrase"]})')
    parser.add_argument('--deploy', action='store_true',
                       help='Deploy applications after importing')
    parser.add_argument('--force', action='store_true',
                       help='Force import by undeploying existing applications first')
    parser.add_argument('--environment', choices=['default'] + list(config.ENVIRONMENTS.keys()),
                       default='default', help='Environment configuration to use')
    
    args = parser.parse_args()
    
    # If environment is specified, override with environment-specific config
    if args.environment != 'default':
        env_config = config.get_import_config(args.environment)
        if args.url == default_config['url']:
            args.url = env_config['url']
        if args.username == default_config['username']:
            args.username = env_config['username']
        if args.password == default_config['password']:
            args.password = env_config['password']
        if args.passphrase == default_config['passphrase']:
            args.passphrase = env_config['passphrase']
    
    print("🚀 Starting Striim Application Importer")
    print(f"   Striim URL: {args.url}")
    print(f"   Import Directory: {args.import_dir}")
    print()
    
    # Step 1: Get list of TQL files
    print("Step 1: Scanning import directory...")
    tql_files = get_tql_files(args.import_dir)
    
    if not tql_files:
        print("✗ No TQL files found in import directory.")
        sys.exit(1)
    
    print(f"✓ Found {len(tql_files)} TQL file(s):")
    for tql_file in tql_files:
        print(f"   • {Path(tql_file).name}")
    print()
    
    # Step 2: Authenticate
    print("Step 2: Authenticating with Striim...")
    api = StriimImportAPI(args.url, args.username, args.password, args.passphrase)
    
    if not api.authenticate():
        print("✗ Authentication failed. Exiting.")
        sys.exit(1)
    
    print(f"✓ Authentication successful. Token: {api.token}")
    print()
    
    # Step 3: Import TQL files
    print("Step 3: Importing applications...")
    imported_count = 0
    deployed_count = 0
    failed_count = 0
    
    for tql_file in tql_files:
        file_name = Path(tql_file).name
        print(f"\n  Processing {file_name}...")
        
        try:
            # Read TQL file
            with open(tql_file, 'r') as f:
                tql_content = f.read()
            
            # Extract app name
            app_name = extract_app_name(tql_content)
            if app_name:
                print(f"    Application name: {app_name}")

                # Get full qualified name (namespace.appname)
                full_app_name = get_full_app_name(tql_file, app_name)
                if full_app_name != app_name:
                    print(f"    Full qualified name: {full_app_name}")

            # Force drop if requested (stop, undeploy, then drop)
            if args.force and app_name:
                full_app_name = get_full_app_name(tql_file, app_name)
                print(f"    Removing existing application ({full_app_name})...")
                print(f"      - Stopping...")
                api.stop_application(full_app_name)
                print(f"      - Undeploying...")
                api.undeploy_application(full_app_name)
                print(f"      - Dropping...")
                api.drop_application(full_app_name)
            
            # Import the TQL
            print(f"    Importing...")
            result = api.import_tql(tql_content)
            
            if result:
                # Check if import was successful
                if isinstance(result, list) and len(result) > 0:
                    first_result = result[0]
                    if first_result.get('executionStatus') == 'Success':
                        print(f"    ✓ Import successful")
                        imported_count += 1
                        
                        # Deploy if requested
                        if args.deploy and app_name:
                            full_app_name = get_full_app_name(tql_file, app_name)
                            print(f"    Deploying application ({full_app_name})...")
                            if api.deploy_application(full_app_name):
                                print(f"    ✓ Deploy successful")
                                deployed_count += 1
                            else:
                                print(f"    ⚠️  Deploy failed")
                    else:
                        error_msg = first_result.get('failureMessage', 'Unknown error')
                        print(f"    ✗ Import failed: {error_msg}")
                        failed_count += 1
                else:
                    print(f"    ✓ Import completed")
                    imported_count += 1
            else:
                print(f"    ✗ Import failed")
                failed_count += 1
                
        except Exception as e:
            print(f"    ✗ Error processing file: {e}")
            failed_count += 1
    
    # Summary
    print(f"\n🎉 Import process complete!")
    print(f"   Total TQL files: {len(tql_files)}")
    print(f"   Successfully imported: {imported_count}")
    if args.deploy:
        print(f"   Successfully deployed: {deployed_count}")
    print(f"   Failed: {failed_count}")


if __name__ == "__main__":
    main()

