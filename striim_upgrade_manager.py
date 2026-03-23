#!/usr/bin/env python3
"""
Striim Upgrade Manager - OP/UDF Handler

Manages the complete lifecycle of OPs and UDFs during Striim upgrades.

UPGRADE WORKFLOW:
1. Find all applications with OPs/UDFs (captures app states: RUNNING/DEPLOYED/CREATED)
2. Export all applications
3. Remove OPs/UDFs from applications (ALTER, DROP, RECOMPILE)
4. Unload OPs/UDFs from Striim
5. [User upgrades Striim]
6. Load new OPs/UDFs
7. Restore OPs/UDFs to applications (ALTER, CREATE, RECOMPILE)
8. Restore application states (DEPLOY/START apps that were running)

Usage:
    # Analyze what needs to be done
    python striim_upgrade_manager.py --analyze

    # Prepare for upgrade (steps 1-4)
    python striim_upgrade_manager.py --prepare-for-upgrade

    # After upgrade, complete the process (steps 6-7)
    python striim_upgrade_manager.py --complete-upgrade

    # Individual steps
    python striim_upgrade_manager.py --export-all
    python striim_upgrade_manager.py --remove-from-apps
    python striim_upgrade_manager.py --unload-components
    python striim_upgrade_manager.py --load-components
    python striim_upgrade_manager.py --restore-to-apps
    python striim_upgrade_manager.py --restore-app-states

    # Status and utilities
    python striim_upgrade_manager.py --status
    python striim_upgrade_manager.py --dry-run --remove-from-apps
    python striim_upgrade_manager.py --reset-state
"""

import requests
import json
import argparse
import re
import sys
import os
import zipfile
import tempfile
from typing import Dict, Optional, List, Tuple, Set
from pathlib import Path
from datetime import datetime
import config
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

STATE_FILE = "upgrade_state.json"
BACKUP_DIR = "upgrade_backup"


class UpgradeState:
    """Manages upgrade state persistence"""

    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.state = self.load()

    def load(self) -> Dict:
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r') as f:
                return json.load(f)
        return {
            'phase': 'initial',
            'timestamp': None,
            'apps_with_components': {},
            'removed_components': {},
            'unloaded_components': [],
            'loaded_components': [],
            'restored_apps': []
        }

    def save(self):
        self.state['timestamp'] = datetime.now().isoformat()
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)
        print(f"[OK] State saved to {self.state_file}")

    def set_phase(self, phase: str):
        self.state['phase'] = phase
        self.save()

    def add_app_component(self, namespace: str, app_name: str, component_type: str,
                         component_name: str, create_statement: str, drop_type: str = None):
        full_app_name = f"{namespace}.{app_name}"
        if full_app_name not in self.state['apps_with_components']:
            self.state['apps_with_components'][full_app_name] = []

        self.state['apps_with_components'][full_app_name].append({
            'type': component_type,
            'name': component_name,
            'create_statement': create_statement,
            'namespace': namespace,
            'app_name': app_name,
            'component_type': drop_type or 'SOURCE'  # For DROP command (SOURCE or OPEN PROCESSOR)
        })


class StriimAPI:
    """Striim API client - reuses patterns from existing scripts"""

    def __init__(self, base_url: str = None, username: str = None, password: str = None):
        default_config = config.get_config()
        self.base_url = base_url or default_config['url']
        self.username = username or default_config['username']
        self.password = password or default_config['password']
        self.token = None

    def authenticate(self) -> bool:
        auth_url = f"{self.base_url}/security/authenticate"
        data = f"username={self.username}&password={self.password}"
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}

        try:
            response = requests.post(auth_url, data=data, headers=headers, verify=False)
            response.raise_for_status()
            result = response.json()
            self.token = result.get("token")

            if self.token:
                print(f"[OK] Authenticated as {self.username}")
                return True
            return False
        except Exception as e:
            print(f"[ERROR] Authentication failed: {e}")
            return False

    def execute_command(self, command: str) -> Optional[Dict]:
        if not self.token:
            print("[ERROR] Not authenticated")
            return None

        api_url = f"{self.base_url}/api/v2/tungsten"
        headers = {
            'authorization': f'STRIIM-TOKEN {self.token}',
            'content-type': 'text/plain'
        }

        try:
            response = requests.post(api_url, headers=headers, data=command, verify=False)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"[ERROR] Command failed: {e}")
            # Try to get more details from the response
            try:
                error_detail = response.json()
                if isinstance(error_detail, list):
                    for cmd_result in error_detail:
                        if cmd_result.get('executionStatus') == 'Failure':
                            failure_msg = cmd_result.get('failureMessage', '')
                            cmd_text = cmd_result.get('command', '')[:100]
                            print(f"[ERROR] Failed command: {cmd_text}...")
                            if failure_msg:
                                print(f"[ERROR] Details: {failure_msg}")
            except:
                pass
            return None

    def export_all_applications(self, export_path: str, passphrase: str) -> bool:
        """Export all applications to a zip file using EXPORT APPLICATION ALL"""
        if not self.token:
            print("[ERROR] Not authenticated")
            return False

        api_url = f"{self.base_url}/api/v2/tungsten"
        headers = {
            'authorization': f'STRIIM-TOKEN {self.token}',
            'content-type': 'text/plain'
        }

        command = f'EXPORT APPLICATION ALL passphrase="{passphrase}";'

        try:
            response = requests.post(api_url, headers=headers, data=command, verify=False)
            response.raise_for_status()

            # The response should be the zip file content
            with open(export_path, 'wb') as f:
                f.write(response.content)

            return True

        except Exception as e:
            print(f"[ERROR] Export failed: {e}")
            return False


class StriimUpgradeManager:
    """Main upgrade manager"""

    def __init__(self, api: StriimAPI, state: UpgradeState, dry_run: bool = False):
        self.api = api
        self.state = state
        self.dry_run = dry_run

    def analyze_from_files(self) -> Dict:
        """Analyze existing exported TQL files without re-exporting"""
        print("\n=== Analyzing from Existing Files ===")

        # Check if backup directory and export file exist
        export_path = os.path.join(BACKUP_DIR, "all_applications.zip")
        if not os.path.exists(export_path):
            print(f"[ERROR] Export file not found: {export_path}")
            print("[INFO] Run --analyze first to export applications")
            return {}

        print(f"[OK] Found existing export: {export_path}")

        # Get list of custom libraries from Striim
        print("\nGetting list of custom libraries...")
        libraries_result = self.api.execute_command("LIST LIBRARIES;")
        if not libraries_result:
            print("[WARN] Failed to get libraries list")
            custom_libraries = set()
        else:
            custom_libraries = set()
            if isinstance(libraries_result, list) and len(libraries_result) > 0:
                output = libraries_result[0].get('output', [])
                for item in output:
                    if 'fileName' in item:
                        filename = item['fileName']
                        base_name = filename.split('-')[0].split('.')[0]
                        custom_libraries.add(base_name)
            print(f"[OK] Found {len(custom_libraries)} custom libraries: {custom_libraries}")

        # Get application states from Striim
        print("\nGetting application states...")
        result = self.api.execute_command("mon;")
        app_states = {}
        if result and isinstance(result, list) and len(result) > 0:
            output = result[0].get('output', {})
            apps = output.get('striimApplications', [])
            for app in apps:
                app_name = app.get('fullName', '')
                app_status = app.get('statusChange', 'UNKNOWN')
                if app_name:
                    app_states[app_name] = app_status
        print(f"[OK] Retrieved {len(app_states)} application(s)")

        # Analyze the existing export file
        print("\nAnalyzing TQL files from export...")
        passphrase = config.get_config().get('passphrase', 'striim123')
        components_found = self._analyze_zip_for_components(export_path, passphrase, custom_libraries)

        # Clear existing component data before saving new analysis
        self.state.state['apps_with_components'] = {}

        # Save to state
        for app_name, components in components_found.items():
            for comp in components:
                parts = app_name.split('.', 1)
                namespace = parts[0] if len(parts) > 1 else 'admin'
                app = parts[1] if len(parts) > 1 else parts[0]
                self.state.add_app_component(
                    namespace, app, comp['type'], comp['name'], comp['create_statement'],
                    drop_type=comp.get('component_type', 'SOURCE')
                )

        # Save application states
        self.state.state['app_states'] = app_states

        self.state.set_phase('analyzed')

        # Display summary
        print(f"\n[OK] Analysis complete. Found components in {len(components_found)} applications")
        for app_name, comps in components_found.items():
            print(f"  {app_name}: {len(comps)} component(s)")
            for comp in comps:
                print(f"    - {comp['type']}: {comp['name']}")

        # Display application states
        print(f"\n[INFO] Application States:")
        for app_name, status in sorted(app_states.items()):
            marker = " *" if app_name in components_found else ""
            print(f"  {app_name}: {status}{marker}")
        if components_found:
            print("\n  * = Contains custom components")

        return components_found

    def analyze(self) -> Dict:
        """Analyze all applications to find OPs and UDFs"""
        if self.dry_run:
            print("\n=== [DRY-RUN] Analyzing Applications ===")
        else:
            print("\n=== Analyzing Applications ===")

        # Check if we have existing component data that would be overwritten
        if self.state.state['apps_with_components'] and not self.dry_run:
            num_apps = len(self.state.state['apps_with_components'])
            total_components = sum(len(comps) for comps in self.state.state['apps_with_components'].values())

            print("\n" + "="*70)
            print("⚠️  WARNING: EXISTING COMPONENT DATA WILL BE OVERWRITTEN!")
            print("="*70)
            print(f"Current state contains {total_components} component(s) across {num_apps} application(s):")
            for app_name, components in self.state.state['apps_with_components'].items():
                print(f"  • {app_name}: {len(components)} component(s)")
                for comp in components:
                    print(f"    - {comp['type']}: {comp['name']}")
            print("\nRe-analyzing will:")
            print("  1. Create a backup at: upgrade_state.json.backup")
            print("  2. REPLACE all component data with current app state")
            print("  3. If components were already removed, you will LOSE the CREATE statements!")
            print("\nThis means you will NOT be able to restore these components unless you")
            print("use the backup file!")
            print("="*70)

            response = input("\nAre you sure you want to continue? (yes/no): ").strip().lower()
            if response not in ['yes', 'y']:
                print("[CANCELLED] Analysis cancelled by user")
                return {}

            # Create backup
            backup_file = f"{self.state.state_file}.backup"
            import shutil
            shutil.copy(self.state.state_file, backup_file)
            print(f"\n[OK] Backed up existing state to {backup_file}")

        # Get all apps and their states using mon command
        result = self.api.execute_command("mon;")
        if not result:
            print("[ERROR] Failed to get application list")
            return {}

        # Extract application states (RUNNING, CREATED, DEPLOYED, etc.)
        app_states = {}
        if isinstance(result, list) and len(result) > 0:
            output = result[0].get('output', {})
            apps = output.get('striimApplications', [])
            for app in apps:
                app_name = app.get('fullName', '')
                app_status = app.get('statusChange', 'UNKNOWN')
                if app_name:
                    app_states[app_name] = app_status

        print(f"[OK] Retrieved {len(app_states)} application(s)")

        # In dry-run mode, show states and exit early
        if self.dry_run:
            print(f"\n[INFO] Application States:")
            for app_name, status in sorted(app_states.items()):
                print(f"  {app_name}: {status}")
            print("\n[DRY-RUN] Would export applications and analyze TQL for custom components")
            return {}

        # Get list of custom libraries
        print("\nGetting list of custom libraries...")
        libraries_result = self.api.execute_command("LIST LIBRARIES;")
        if not libraries_result:
            print("[WARN] Failed to get libraries list")
            custom_libraries = set()
        else:
            # Extract library names from the response
            custom_libraries = set()
            if isinstance(libraries_result, list) and len(libraries_result) > 0:
                output = libraries_result[0].get('output', [])
                for item in output:
                    if 'fileName' in item:
                        # Extract base name without version and extension
                        # e.g., "StriimWatcher-5.2.0.4.jar" -> "StriimWatcher"
                        filename = item['fileName']
                        base_name = filename.split('-')[0].split('.')[0]
                        custom_libraries.add(base_name)
            print(f"[OK] Found {len(custom_libraries)} custom libraries: {custom_libraries}")

        # Export all to analyze TQL
        print("\nExporting applications to analyze...")
        passphrase = config.get_config().get('passphrase', 'striim123')

        # Create backup directory if it doesn't exist
        Path(BACKUP_DIR).mkdir(exist_ok=True)
        export_path = os.path.join(BACKUP_DIR, "all_applications.zip")

        if not self.api.export_all_applications(export_path, passphrase):
            print("[ERROR] Export failed")
            return {}

        print(f"[OK] Exported to {export_path}")

        # Extract and parse TQL files from zip
        components_found = self._analyze_zip_for_components(export_path, passphrase, custom_libraries)

        # Clear existing component data before saving new analysis
        self.state.state['apps_with_components'] = {}

        # Save to state
        for app_name, components in components_found.items():
            for comp in components:
                parts = app_name.split('.', 1)
                namespace = parts[0] if len(parts) > 1 else 'admin'
                app = parts[1] if len(parts) > 1 else parts[0]
                self.state.add_app_component(
                    namespace, app, comp['type'], comp['name'], comp['create_statement'],
                    drop_type=comp.get('component_type', 'SOURCE')
                )

        # Save application states
        self.state.state['app_states'] = app_states

        self.state.set_phase('analyzed')

        # Display summary
        print(f"\n[OK] Analysis complete. Found components in {len(components_found)} applications")
        for app_name, comps in components_found.items():
            print(f"  {app_name}: {len(comps)} component(s)")
            for comp in comps:
                print(f"    - {comp['type']}: {comp['name']}")

        # Display application states
        print(f"\n[INFO] Application States:")
        for app_name, status in sorted(app_states.items()):
            # Highlight apps with custom components
            marker = " *" if app_name in components_found else ""
            print(f"  {app_name}: {status}{marker}")
        if components_found:
            print("\n  * = Contains custom components")

        return components_found

    def _analyze_zip_for_components(self, zip_path: str, passphrase: str, custom_libraries: set) -> Dict:
        """Extract TQL files from zip and analyze for components"""
        components = {}

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # Extract all TQL files
                tql_files = [f for f in zip_ref.namelist() if f.endswith('.tql')]

                if not tql_files:
                    print("[WARN] No TQL files found in export")
                    return {}

                print(f"[OK] Found {len(tql_files)} TQL files in export")

                # Analyze each TQL file
                for tql_file in tql_files:
                    tql_content = zip_ref.read(tql_file).decode('utf-8')
                    file_components = self._analyze_tql_for_components(tql_content, custom_libraries)

                    # Merge components from this file
                    for app_name, comps in file_components.items():
                        if app_name not in components:
                            components[app_name] = []
                        components[app_name].extend(comps)

                return components

        except Exception as e:
            print(f"[ERROR] Failed to analyze zip file: {e}")
            return {}

    def _analyze_tql_for_components(self, tql_content: str, custom_libraries: set) -> Dict:
        """Parse TQL to find OPs and UDFs"""
        components = {}

        # Patterns for CREATE statements - match with optional namespace
        # Matches: CREATE SOURCE name USING or CREATE SOURCE namespace.name USING
        op_pattern = r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:OPEN\s+)?(?:SOURCE\s+|PROCESSOR\s+)(?:(\w+)\.)?(\w+)\s+USING\s+(\S+)'
        # UDF calls pattern: matches multi-part function calls like com.striim.util.AdvFormat.FormatAllDates(...)
        # Must have at least 3 parts (package.class.method) to distinguish from Striim built-ins
        udf_call_pattern = r'\b([a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*){2,})\s*\('
        app_pattern = r'CREATE\s+(?:OR\s+REPLACE\s+)?APPLICATION\s+(?:(\w+)\.)?(\w+)'
        cq_pattern = r'CREATE\s+(?:OR\s+REPLACE\s+)?CQ\s+(?:(\w+)\.)?(\w+)'

        # Find all applications first
        apps_found = set()
        for match in re.finditer(app_pattern, tql_content, re.IGNORECASE):
            namespace, app_name = match.groups()
            if namespace:
                apps_found.add(f"{namespace}.{app_name}")
            else:
                apps_found.add(app_name)

        # Find all OPs (including SOURCEs using custom adapters)
        for match in re.finditer(op_pattern, tql_content, re.IGNORECASE | re.MULTILINE):
            namespace, name, adapter = match.groups()

            # Only process custom OPs - check if adapter matches a custom library
            if not adapter.startswith('Global.'):
                continue

            # Extract adapter name from Global.AdapterName
            adapter_name = adapter.split('.')[1] if '.' in adapter else adapter

            # Fuzzy match: check if adapter name matches any custom library (case-insensitive, partial match)
            is_custom = False
            for lib in custom_libraries:
                # Check if library name is in adapter name or vice versa (case-insensitive)
                if lib.lower() in adapter_name.lower() or adapter_name.lower() in lib.lower():
                    is_custom = True
                    break

            if not is_custom:
                continue

            # Store simple name for DROP, full name for display
            simple_name = name
            full_name = f"{namespace}.{name}" if namespace else name

            # Determine if this is an OPEN PROCESSOR or SOURCE
            # Look back in the match to see if it has "OPEN" keyword
            match_text = match.group(0)
            is_open_processor = 'OPEN' in match_text.upper()
            component_type = 'OPEN PROCESSOR' if is_open_processor else 'SOURCE'

            # Try to find which app this belongs to
            app_name = self._find_app_for_component(tql_content, namespace, name, apps_found)
            if app_name:
                if app_name not in components:
                    components[app_name] = []
                components[app_name].append({
                    'type': 'OP',
                    'name': full_name,
                    'simple_name': simple_name,  # For DROP command
                    'component_type': component_type,  # For DROP command (SOURCE or OPEN PROCESSOR)
                    'adapter': adapter,
                    'create_statement': self._extract_full_statement(tql_content, match.start())
                })

        # Find all UDF calls within CQ statements
        # UDFs are Java functions called within CQs, not created with CREATE FUNCTION
        # Pattern: com.package.class.method(...) - must have at least 3 parts
        udf_calls_found = set()
        for match in re.finditer(udf_call_pattern, tql_content, re.IGNORECASE):
            udf_full_name = match.group(1)

            # Only track custom UDFs (check against custom libraries)
            # Extract the package/class prefix (e.g., "com.striim.util" from "com.striim.util.AdvFormat")
            parts = udf_full_name.split('.')
            if len(parts) < 3:
                continue

            # Check if any custom library matches the UDF package
            is_custom = False
            for lib in custom_libraries:
                # Fuzzy match: check if library name appears in UDF package
                lib_lower = lib.lower()
                udf_lower = udf_full_name.lower()
                if lib_lower in udf_lower or any(lib_lower in part for part in parts):
                    is_custom = True
                    break

            if not is_custom:
                continue

            udf_calls_found.add(udf_full_name)

        # For each UDF call found, determine which CQ and app it belongs to
        for udf_name in udf_calls_found:
            # Find the CQ that contains this UDF call
            cq_info = self._find_cq_for_udf(tql_content, udf_name, apps_found)
            if cq_info:
                app_name, cq_name, cq_statement = cq_info
                if app_name not in components:
                    components[app_name] = []

                # Check if we already have this CQ (might have multiple UDFs)
                existing_cq = None
                for comp in components[app_name]:
                    if comp.get('type') == 'CQ' and comp.get('name') == cq_name:
                        existing_cq = comp
                        break

                if existing_cq:
                    # Add UDF to existing CQ's UDF list
                    if 'udfs' not in existing_cq:
                        existing_cq['udfs'] = []
                    if udf_name not in existing_cq['udfs']:
                        existing_cq['udfs'].append(udf_name)
                else:
                    # Create new CQ entry
                    components[app_name].append({
                        'type': 'CQ',
                        'name': cq_name,
                        'simple_name': cq_name.split('.')[-1],  # For DROP command
                        'component_type': 'CQ',
                        'udfs': [udf_name],
                        'create_statement': cq_statement
                    })

        return components

    def _extract_full_statement(self, tql: str, start_pos: int) -> str:
        """Extract full CREATE statement ending with semicolon"""
        end_pos = tql.find(';', start_pos)
        if end_pos == -1:
            end_pos = len(tql)
        return tql[start_pos:end_pos+1].strip()

    def _find_cq_for_udf(self, tql: str, udf_name: str, apps_found: Set[str]) -> Optional[Tuple[str, str, str]]:
        """Find which CQ contains a UDF call and which app it belongs to
        Returns: (app_name, cq_name, cq_statement) or None
        """
        # Pattern to find CQ statements
        cq_pattern = r'CREATE\s+(?:OR\s+REPLACE\s+)?CQ\s+(?:(\w+)\.)?(\w+)\s+(.*?);;'

        for match in re.finditer(cq_pattern, tql, re.IGNORECASE | re.DOTALL):
            namespace, cq_name, cq_body = match.groups()

            # Check if this CQ contains the UDF call
            if udf_name in cq_body:
                # Find which app this CQ belongs to
                full_cq_name = f"{namespace}.{cq_name}" if namespace else cq_name
                app_name = self._find_app_for_component(tql, namespace, cq_name, apps_found)

                if app_name:
                    # Extract the full CQ statement
                    cq_statement = self._extract_full_statement(tql, match.start())
                    return (app_name, full_cq_name, cq_statement)

        return None

    def _find_app_for_component(self, tql: str, namespace: Optional[str], comp_name: str, apps_found: Set[str]) -> Optional[str]:
        """Find which application a component belongs to"""
        # Look for the component in the TQL (with or without namespace)
        if namespace:
            comp_pattern = rf'\b(?:{namespace}\.)?{comp_name}\b'
        else:
            comp_pattern = rf'\b{comp_name}\b'

        comp_match = re.search(comp_pattern, tql, re.IGNORECASE)
        if not comp_match:
            return None

        comp_pos = comp_match.start()

        # Find all app declarations before this position
        app_pattern = r'CREATE\s+(?:OR\s+REPLACE\s+)?APPLICATION\s+(?:(\w+)\.)?(\w+)'
        best_app = None
        best_pos = -1

        for match in re.finditer(app_pattern, tql[:comp_pos], re.IGNORECASE):
            if match.start() > best_pos:
                best_pos = match.start()
                ns, app = match.groups()
                if ns:
                    best_app = f"{ns}.{app}"
                else:
                    # If no namespace in app declaration, use 'admin' as default
                    best_app = f"admin.{app}"

        return best_app

    def remove_from_apps(self):
        """Remove OPs/UDFs from applications"""
        print("\n=== Removing Components from Applications ===")

        if not self.state.state['apps_with_components']:
            print("[WARN] No components found. Run --analyze first.")
            return

        for app_name, components in self.state.state['apps_with_components'].items():
            print(f"\nProcessing {app_name}...")

            # Check app state - if RUNNING, need to STOP first, then UNDEPLOY
            app_state = self.state.state.get('app_states', {}).get(app_name, 'UNKNOWN')

            if app_state == 'RUNNING':
                if self.dry_run:
                    print(f"  [DRY-RUN] Would stop {app_name} (currently RUNNING)")
                else:
                    print(f"  Stopping {app_name}...")
                    self.api.execute_command(f"STOP APPLICATION {app_name};")

            # Undeploy app (whether it was RUNNING or DEPLOYED)
            if app_state in ['RUNNING', 'DEPLOYED']:
                if self.dry_run:
                    print(f"  [DRY-RUN] Would undeploy {app_name}")
                else:
                    print(f"  Undeploying {app_name}...")
                    self.api.execute_command(f"UNDEPLOY APPLICATION {app_name};")

            for comp in components:
                comp_name = comp['name']
                comp_type = comp['type']
                component_type = comp.get('component_type', 'SOURCE')  # SOURCE or OPEN PROCESSOR

                if self.dry_run:
                    print(f"  [DRY-RUN] Would remove {comp_type} {comp_name}")
                    continue

                # Send ALTER, DROP, RECOMPILE as a single batch command
                print(f"  Removing {comp_type} {comp_name}...")

                # DROP command needs the component type (SOURCE or OPEN PROCESSOR)
                drop_cmd = f"DROP {component_type} {comp_name};"
                batch_cmd = f"ALTER APPLICATION {app_name};\n{drop_cmd}\nALTER APPLICATION {app_name} RECOMPILE;"
                result = self.api.execute_command(batch_cmd)

                if result:
                    self.state.state['removed_components'].setdefault(app_name, []).append(comp_name)
                    print(f"  [OK] Removed {comp_name}")
                else:
                    print(f"  [ERROR] Failed to remove {comp_name}")

        if not self.dry_run:
            self.state.set_phase('components_removed')
        print("\n[OK] All components removed from applications")

    def unload_components(self):
        """Unload OPs/UDFs from Striim"""
        print("\n=== Unloading Components from Striim ===")

        if not self.state.state['removed_components']:
            print("[WARN] No removed components found. Run --remove-from-apps first.")
            return

        # Get unique component names across all apps
        all_components = set()
        for app_name, components in self.state.state['apps_with_components'].items():
            for comp in components:
                all_components.add((comp['name'], comp['type']))

        for comp_name, comp_type in all_components:
            if self.dry_run:
                print(f"  [DRY-RUN] Would unload {comp_type} {comp_name}")
                continue

            print(f"  Unloading {comp_type} {comp_name}...")
            unload_cmd = f"UNLOAD OPEN PROCESSOR '{comp_name}';" if comp_type == 'OP' else f"UNLOAD UDF '{comp_name}';"
            result = self.api.execute_command(unload_cmd)

            if result:
                self.state.state['unloaded_components'].append(comp_name)
                print(f"  [OK] Unloaded {comp_name}")
            else:
                print(f"  [WARN] Failed to unload {comp_name}")

        if not self.dry_run:
            self.state.set_phase('components_unloaded')
        print("\n[OK] All components unloaded")

    def load_components(self, component_path: str = None):
        """Load new OPs/UDFs after upgrade"""
        print("\n=== Loading Components ===")

        if component_path:
            # Load specific component (manual mode)
            if self.dry_run:
                print(f"  [DRY-RUN] Would load {component_path}")
                return

            print(f"  Loading {component_path}...")
            load_cmd = f"LOAD OPEN PROCESSOR '{component_path}';"
            result = self.api.execute_command(load_cmd)

            if result:
                self.state.state['loaded_components'].append(component_path)
                self.state.save()
                print(f"  [OK] Loaded {component_path}")
            else:
                print(f"  [ERROR] Failed to load {component_path}")
        else:
            # Interactive mode - map old components to new versions
            self._interactive_component_loading()

    def _interactive_component_loading(self):
        """Interactive mode to map old component versions to new ones"""
        # Get list of components that were unloaded
        if not self.state.state.get('unloaded_components'):
            print("[WARN] No components were unloaded. Nothing to load.")
            print("[INFO] If you want to load a specific component, use:")
            print("       --load-components --component-path UploadedFiles/YourComponent.jar")
            return

        unloaded = self.state.state['unloaded_components']
        print(f"\n[INFO] Found {len(unloaded)} component(s) that were unloaded:")
        for comp in unloaded:
            print(f"  - {comp}")

        # Get list of available files in UploadedFiles/
        print("\n[INFO] Fetching available files from UploadedFiles/...")
        available_files = self._get_uploaded_files()

        if not available_files:
            print("[ERROR] No files found in UploadedFiles/")
            print("[INFO] Please upload your new component files via Striim UI first")
            return

        print(f"[OK] Found {len(available_files)} file(s) in UploadedFiles/:")
        for f in available_files:
            print(f"  - {f}")

        # Build mapping with auto-suggestions
        print("\n=== Component Mapping ===")
        print("Mapping old components to new versions...\n")

        component_mapping = {}
        for old_comp in unloaded:
            # Extract base name (e.g., "StriimWatcher" from "StriimWatcher-5.2.0.5.jar")
            base_name = self._extract_base_name(old_comp)

            # Find matching files
            matches = [f for f in available_files if base_name.lower() in f.lower()]

            if len(matches) == 1:
                # Auto-match if only one candidate
                suggested = matches[0]
                print(f"Old: {old_comp}")
                print(f"New: {suggested} (auto-matched)")

                if not self.dry_run:
                    confirm = input("Accept this mapping? (yes/no/manual): ").strip().lower()
                    if confirm == 'yes' or confirm == 'y':
                        component_mapping[old_comp] = f"UploadedFiles/{suggested}"
                    elif confirm == 'manual' or confirm == 'm':
                        component_mapping[old_comp] = self._manual_file_selection(old_comp, available_files)
                    else:
                        print(f"  [SKIP] Skipping {old_comp}")
                else:
                    component_mapping[old_comp] = f"UploadedFiles/{suggested}"

            elif len(matches) > 1:
                # Multiple matches - let user choose
                print(f"Old: {old_comp}")
                print(f"Found {len(matches)} possible matches:")
                for i, match in enumerate(matches, 1):
                    print(f"  {i}. {match}")

                if not self.dry_run:
                    choice = input(f"Select 1-{len(matches)} or 's' to skip: ").strip()
                    if choice.lower() == 's':
                        print(f"  [SKIP] Skipping {old_comp}")
                    elif choice.isdigit() and 1 <= int(choice) <= len(matches):
                        selected = matches[int(choice) - 1]
                        component_mapping[old_comp] = f"UploadedFiles/{selected}"
                        print(f"  [OK] Mapped to {selected}")
                    else:
                        print(f"  [SKIP] Invalid choice, skipping {old_comp}")
                else:
                    # In dry-run, use first match
                    component_mapping[old_comp] = f"UploadedFiles/{matches[0]}"
            else:
                # No auto-match found
                print(f"Old: {old_comp}")
                print(f"No automatic match found for base name '{base_name}'")

                if not self.dry_run:
                    component_mapping[old_comp] = self._manual_file_selection(old_comp, available_files)
                else:
                    print(f"  [SKIP] Would prompt for manual selection")

            print()  # Blank line between components

        # Show final mapping
        if component_mapping:
            print("\n=== Final Component Mapping ===")
            for old, new in component_mapping.items():
                print(f"  {old} -> {new}")

            # Load all components
            if self.dry_run:
                print("\n[DRY-RUN] Would load the above components")
            else:
                print("\n[INFO] Loading components...")
                for old_comp, new_path in component_mapping.items():
                    print(f"\nLoading {new_path}...")
                    load_cmd = f"LOAD OPEN PROCESSOR '{new_path}';"
                    result = self.api.execute_command(load_cmd)

                    if result:
                        self.state.state['loaded_components'].append(new_path)
                        print(f"  [OK] Loaded {new_path}")
                    else:
                        print(f"  [ERROR] Failed to load {new_path}")

                self.state.save()
                print("\n[OK] Component loading complete")
        else:
            print("[WARN] No components were mapped")

    def _extract_base_name(self, filename: str) -> str:
        """Extract base name from versioned filename
        Examples:
          StriimWatcher-5.2.0.5.jar -> StriimWatcher
          MyAdapter-1.0.jar -> MyAdapter
          CustomOP.scm -> CustomOP
        """
        # Remove path if present
        name = filename.split('/')[-1]
        # Remove extension
        name = name.rsplit('.', 1)[0]
        # Remove version (everything after first dash or underscore)
        for sep in ['-', '_']:
            if sep in name:
                name = name.split(sep)[0]
                break
        return name

    def _get_uploaded_files(self) -> List[str]:
        """Get list of files in UploadedFiles/ directory via API"""
        # Use LIST LIBRARIES to see what's available
        # This shows files that have been uploaded
        result = self.api.execute_command("LIST LIBRARIES;")

        files = []
        if result and isinstance(result, list) and len(result) > 0:
            output = result[0].get('output', [])
            for item in output:
                if 'fileName' in item:
                    files.append(item['fileName'])

        return files

    def _manual_file_selection(self, old_comp: str, available_files: List[str]) -> Optional[str]:
        """Let user manually select a file from the list"""
        print(f"Available files:")
        for i, f in enumerate(available_files, 1):
            print(f"  {i}. {f}")

        choice = input(f"Select 1-{len(available_files)} or 's' to skip: ").strip()
        if choice.lower() == 's':
            print(f"  [SKIP] Skipping {old_comp}")
            return None
        elif choice.isdigit() and 1 <= int(choice) <= len(available_files):
            selected = available_files[int(choice) - 1]
            print(f"  [OK] Mapped to {selected}")
            return f"UploadedFiles/{selected}"
        else:
            print(f"  [SKIP] Invalid choice, skipping {old_comp}")
            return None

    def restore_to_apps(self):
        """Restore OPs/UDFs to applications"""
        print("\n=== Restoring Components to Applications ===")

        if not self.state.state['apps_with_components']:
            print("[WARN] No components to restore. Run --analyze first.")
            return

        for app_name, components in self.state.state['apps_with_components'].items():
            print(f"\nProcessing {app_name}...")

            # Check if app is deployed/running - if so, need to undeploy first
            app_state = self.state.state.get('app_states', {}).get(app_name, 'UNKNOWN')

            if app_state in ['RUNNING', 'DEPLOYED']:
                if self.dry_run:
                    print(f"  [DRY-RUN] Would undeploy {app_name} (currently {app_state})")
                else:
                    # Stop if running
                    if app_state == 'RUNNING':
                        print(f"  Stopping {app_name}...")
                        self.api.execute_command(f"STOP APPLICATION {app_name};")

                    # Undeploy
                    print(f"  Undeploying {app_name}...")
                    self.api.execute_command(f"UNDEPLOY APPLICATION {app_name};")

            for comp in components:
                comp_name = comp['name']
                comp_type = comp['type']
                create_stmt = comp['create_statement']
                component_type = comp['component_type']  # SOURCE or OPEN PROCESSOR
                simple_name = comp.get('simple_name', comp_name)  # Use simple name for DROP

                if self.dry_run:
                    print(f"  [DRY-RUN] Would restore {comp_type} {comp_name}")
                    continue

                # Restore component in steps
                print(f"  Restoring {comp_type} {comp_name}...")

                # Step 1: ALTER APPLICATION
                alter_cmd = f"ALTER APPLICATION {app_name};"
                self.api.execute_command(alter_cmd)

                # Step 2: Try to DROP first (in case it exists in a ghost state)
                # Use simple name for DROP (without namespace)
                drop_cmd = f"DROP {component_type} {simple_name};"
                self.api.execute_command(drop_cmd)  # Ignore errors if it doesn't exist

                # Step 3: CREATE the component (use CREATE OR REPLACE if possible)
                # Replace CREATE with CREATE OR REPLACE to handle existing components
                if create_stmt.strip().upper().startswith('CREATE SOURCE'):
                    create_stmt_safe = create_stmt.replace('CREATE SOURCE', 'CREATE OR REPLACE SOURCE', 1)
                elif create_stmt.strip().upper().startswith('CREATE OPEN PROCESSOR'):
                    create_stmt_safe = create_stmt.replace('CREATE OPEN PROCESSOR', 'CREATE OR REPLACE OPEN PROCESSOR', 1)
                elif create_stmt.strip().upper().startswith('CREATE CQ'):
                    create_stmt_safe = create_stmt.replace('CREATE CQ', 'CREATE OR REPLACE CQ', 1)
                else:
                    create_stmt_safe = create_stmt

                result = self.api.execute_command(create_stmt_safe)

                # Step 4: RECOMPILE
                recompile_cmd = f"ALTER APPLICATION {app_name} RECOMPILE;"
                self.api.execute_command(recompile_cmd)

                if result:
                    self.state.state['restored_apps'].append(app_name)
                    print(f"  [OK] Restored {comp_name}")
                else:
                    print(f"  [ERROR] Failed to restore {comp_name}")
                    # Print more debug info
                    if result and isinstance(result, list) and len(result) > 0:
                        failure_msg = result[0].get('failureMessage', 'Unknown error')
                        print(f"  [ERROR] Details: {failure_msg}")

        if not self.dry_run:
            self.state.set_phase('components_restored')
        print("\n[OK] All components restored to applications")

    def restore_app_states(self):
        """Restore applications to their original states (DEPLOYED/RUNNING)"""
        print("\n=== Restoring Application States ===")

        if not self.state.state.get('app_states'):
            print("[WARN] No application states found. Run --analyze first.")
            return

        if not self.state.state['apps_with_components']:
            print("[WARN] No components were restored. Run --restore first.")
            return

        app_states = self.state.state['app_states']
        apps_with_components = set(self.state.state['apps_with_components'].keys())

        # Track which apps need state restoration
        apps_to_deploy = []
        apps_to_start = []

        for app_name in apps_with_components:
            original_state = app_states.get(app_name, 'UNKNOWN')

            if original_state == 'DEPLOYED':
                apps_to_deploy.append(app_name)
            elif original_state == 'RUNNING':
                apps_to_start.append(app_name)
            elif original_state == 'CREATED':
                # Already in correct state, no action needed
                pass
            else:
                print(f"[WARN] Unknown state '{original_state}' for {app_name}, skipping")

        # Display what will be done
        if apps_to_deploy:
            print(f"\nApplications to DEPLOY ({len(apps_to_deploy)}):")
            for app_name in apps_to_deploy:
                print(f"  - {app_name}")

        if apps_to_start:
            print(f"\nApplications to DEPLOY and START ({len(apps_to_start)}):")
            for app_name in apps_to_start:
                print(f"  - {app_name}")

        if not apps_to_deploy and not apps_to_start:
            print("\n[INFO] All applications are already in CREATED state, no action needed")
            return

        if self.dry_run:
            print("\n[DRY-RUN] Would restore application states")
            return

        # Deploy applications
        for app_name in apps_to_deploy:
            print(f"\nDeploying {app_name}...")
            result = self.api.execute_command(f"DEPLOY APPLICATION {app_name};")
            if result:
                print(f"  [OK] Deployed {app_name}")
            else:
                print(f"  [ERROR] Failed to deploy {app_name}")

        # Deploy and start applications
        for app_name in apps_to_start:
            print(f"\nDeploying and starting {app_name}...")

            # Deploy first
            result = self.api.execute_command(f"DEPLOY APPLICATION {app_name};")
            if not result:
                print(f"  [ERROR] Failed to deploy {app_name}")
                continue
            print(f"  [OK] Deployed {app_name}")

            # Then start
            result = self.api.execute_command(f"START APPLICATION {app_name};")
            if result:
                print(f"  [OK] Started {app_name}")
            else:
                print(f"  [ERROR] Failed to start {app_name}")

        self.state.set_phase('states_restored')
        print("\n[OK] Application states restored")

    def prepare_for_upgrade(self):
        """Run all pre-upgrade steps"""
        print("\n" + "="*60)
        print("PREPARE FOR UPGRADE - Running all pre-upgrade steps")
        print("="*60)

        self.analyze()
        self.remove_from_apps()
        self.unload_components()

        print("\n" + "="*60)
        print("PRE-UPGRADE COMPLETE")
        print("="*60)
        print("\nNext steps:")
        print("1. Upgrade Striim to the new version")
        print("2. Upload new OP/UDF files via UI")
        print("3. Run: python striim_upgrade_manager.py --complete-upgrade")

    def complete_upgrade(self):
        """Run all post-upgrade steps"""
        print("\n" + "="*60)
        print("COMPLETE UPGRADE - Running all post-upgrade steps")
        print("="*60)

        print("\n[INFO] Make sure you have uploaded the new component files!")
        print("[INFO] Use --load-components --component-path <path> for each component")
        print("\nThen run --restore-to-apps to add them back to applications")


def main():
    parser = argparse.ArgumentParser(
        description='Striim Upgrade Manager - OP/UDF Handler',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze what needs to be done (exports and analyzes)
  python striim_upgrade_manager.py --analyze

  # Re-analyze from existing exported files (no re-export)
  python striim_upgrade_manager.py --analyze-from-files

  # Quick check: view app states without full analysis
  python striim_upgrade_manager.py --dry-run --analyze

  # Prepare for upgrade (all pre-upgrade steps)
  python striim_upgrade_manager.py --prepare-for-upgrade

  # Individual steps
  python striim_upgrade_manager.py --remove-from-apps
  python striim_upgrade_manager.py --unload-components

  # After upgrade
  python striim_upgrade_manager.py --load-components --component-path UploadedFiles/MyOP.scm
  python striim_upgrade_manager.py --restore-to-apps
  python striim_upgrade_manager.py --restore-app-states

  # Dry run mode (preview any action)
  python striim_upgrade_manager.py --dry-run --remove-from-apps
  python striim_upgrade_manager.py --dry-run --restore-app-states

  # Check status
  python striim_upgrade_manager.py --status
        """
    )

    # Actions
    parser.add_argument('--analyze', action='store_true',
                       help='Analyze apps for OPs/UDFs (exports and analyzes)')
    parser.add_argument('--analyze-from-files', action='store_true',
                       help='Re-analyze from existing exported files (no re-export)')
    parser.add_argument('--remove-from-apps', action='store_true',
                       help='Remove OPs/UDFs from apps (ALTER, DROP, RECOMPILE)')
    parser.add_argument('--unload-components', action='store_true',
                       help='Unload OPs/UDFs from Striim')
    parser.add_argument('--load-components', action='store_true',
                       help='Load new OPs/UDFs (requires --component-path)')
    parser.add_argument('--restore-to-apps', action='store_true',
                       help='Restore OPs/UDFs to apps (ALTER, CREATE, RECOMPILE)')
    parser.add_argument('--restore-app-states', action='store_true',
                       help='Restore applications to their original states (DEPLOYED/RUNNING)')
    parser.add_argument('--prepare-for-upgrade', action='store_true',
                       help='Run all pre-upgrade steps (analyze, remove, unload)')
    parser.add_argument('--complete-upgrade', action='store_true',
                       help='Show post-upgrade instructions')
    parser.add_argument('--status', action='store_true',
                       help='Show current upgrade status')
    parser.add_argument('--reset-state', action='store_true',
                       help='Reset upgrade state (WARNING: deletes state file)')

    # Options
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be done without doing it')
    parser.add_argument('--component-path',
                       help='Path to component file (e.g., UploadedFiles/MyOP.scm)')
    parser.add_argument('--component-type', choices=['OP', 'UDF'],
                       help='Component type (for reference)')

    args = parser.parse_args()

    # Handle status and reset without authentication
    if args.status:
        state = UpgradeState()
        print("\n=== Upgrade State ===")
        print(json.dumps(state.state, indent=2))
        return

    if args.reset_state:
        if os.path.exists(STATE_FILE):
            backup_name = f"{STATE_FILE}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            os.rename(STATE_FILE, backup_name)
            print(f"[OK] State backed up to {backup_name}")
        else:
            print("[INFO] No state file to reset")
        return

    # Initialize API and state
    state = UpgradeState()
    api = StriimAPI()

    if not api.authenticate():
        print("[ERROR] Authentication failed. Check config.py settings.")
        sys.exit(1)

    manager = StriimUpgradeManager(api, state, args.dry_run)

    # Execute actions
    try:
        if args.analyze:
            manager.analyze()
        elif args.analyze_from_files:
            manager.analyze_from_files()
        elif args.remove_from_apps:
            manager.remove_from_apps()
        elif args.unload_components:
            manager.unload_components()
        elif args.load_components:
            manager.load_components(args.component_path)
        elif args.restore_to_apps:
            manager.restore_to_apps()
        elif args.restore_app_states:
            manager.restore_app_states()
        elif args.prepare_for_upgrade:
            manager.prepare_for_upgrade()
        elif args.complete_upgrade:
            manager.complete_upgrade()
        else:
            parser.print_help()
            print("\n[INFO] No action specified. Use one of the action flags above.")
    except KeyboardInterrupt:
        print("\n\n[WARN] Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

