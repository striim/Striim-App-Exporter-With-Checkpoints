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
    python striim_upgrade_manager.py --load-components --component-path "file1.jar,file2.jar,file3.jar"
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
import logging
from typing import Dict, Optional, List, Tuple, Set
from pathlib import Path
from datetime import datetime
import config
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

STATE_FILE = "upgrade_state.json"
BACKUP_DIR = "upgrade_backup"
LOG_DIR = "upgrade_logs"

logger = logging.getLogger("striim_upgrade_manager")


def setup_logging(log_dir: str = LOG_DIR, verbose: bool = False):
    """Setup logging with both console and file output.

    Console shows INFO+ (or DEBUG+ if verbose).
    File always captures DEBUG level with timestamps for full audit trail.
    Log files are timestamped so multiple runs are preserved.
    """
    log_level = logging.DEBUG if verbose else logging.INFO
    log_format = '%(asctime)s [%(levelname)s] %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

    # Console handler - same output as before (replaces print)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(log_format, date_format))

    handlers = [console_handler]

    # File handler - always DEBUG, timestamped filename
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = os.path.join(log_dir, f"upgrade_manager_{timestamp}.log")
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format, date_format))
    handlers.append(file_handler)

    # Configure root logger
    logging.basicConfig(
        level=logging.DEBUG,
        format=log_format,
        datefmt=date_format,
        handlers=handlers
    )

    logger.info(f"Logging to file: {log_file}")


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
            'restored_apps': [],
            'library_files': {}  # Map base_name -> filename (e.g., AdvFormat -> AdvFormat-5.0.2.jar)
        }

    def save(self):
        self.state['timestamp'] = datetime.now().isoformat()
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)
        logger.info(f"[OK] State saved to {self.state_file}")

    def set_phase(self, phase: str):
        self.state['phase'] = phase
        self.save()

    def add_app_component(self, namespace: str, app_name: str, component_type: str,
                         component_name: str, create_statement: str, drop_type: str = None,
                         flow: str = None, simple_name: str = None, udfs: list = None):
        full_app_name = f"{namespace}.{app_name}"
        if full_app_name not in self.state['apps_with_components']:
            self.state['apps_with_components'][full_app_name] = []

        # Clean newlines from create_statement - replace with spaces
        # This prevents issues when executing commands via API
        clean_statement = ' '.join(create_statement.split())

        component_data = {
            'type': component_type,
            'name': component_name,
            'simple_name': simple_name or component_name.split('.')[-1],  # For DROP command
            'create_statement': clean_statement,
            'namespace': namespace,
            'app_name': app_name,
            'component_type': drop_type or 'SOURCE',  # For DROP command (SOURCE or OPEN PROCESSOR)
            'flow': flow  # Flow name if inside a FLOW block, None otherwise
        }

        # Add UDFs field for CQ components
        if udfs:
            component_data['udfs'] = udfs

        self.state['apps_with_components'][full_app_name].append(component_data)


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
                logger.info(f"[OK] Authenticated as {self.username}")
                return True
            return False
        except Exception as e:
            logger.error(f"[ERROR] Authentication failed: {e}")
            return False

    def execute_command(self, command: str) -> Optional[Dict]:
        if not self.token:
            logger.error("[ERROR] Not authenticated")
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
            logger.error(f"[ERROR] Command failed: {e}")
            # Try to get more details from the response
            try:
                error_detail = response.json()
                if isinstance(error_detail, list):
                    for cmd_result in error_detail:
                        if cmd_result.get('executionStatus') == 'Failure':
                            failure_msg = cmd_result.get('failureMessage', '')
                            cmd_text = cmd_result.get('command', '')[:100]
                            logger.error(f"[ERROR] Failed command: {cmd_text}...")
                            if failure_msg:
                                logger.error(f"[ERROR] Details: {failure_msg}")
            except:
                pass
            return None

    def export_all_applications(self, export_path: str, passphrase: str) -> bool:
        """Export all applications to a zip file using EXPORT APPLICATION ALL"""
        if not self.token:
            logger.error("[ERROR] Not authenticated")
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
            logger.error(f"[ERROR] Export failed: {e}")
            return False


class StriimUpgradeManager:
    """Main upgrade manager"""

    def __init__(self, api: StriimAPI, state: UpgradeState, dry_run: bool = False):
        self.api = api
        self.state = state
        self.dry_run = dry_run

    def analyze_from_files(self) -> Dict:
        """Analyze existing exported TQL files without re-exporting"""
        logger.info("\n=== Analyzing from Existing Files ===")

        # Check for extracted directory first, then fall back to zip
        extracted_dir = os.path.join(BACKUP_DIR, "all_applications")
        export_path = os.path.join(BACKUP_DIR, "all_applications.zip")

        use_directory = False
        if os.path.isdir(extracted_dir):
            # Count TQL files in directory
            tql_files = [f for f in os.listdir(extracted_dir) if f.endswith('.tql')]
            if tql_files:
                logger.info(f"[OK] Found extracted directory with {len(tql_files)} TQL files: {extracted_dir}")
                use_directory = True
            else:
                logger.warning(f"[WARN] Directory exists but contains no TQL files: {extracted_dir}")

        if not use_directory:
            if not os.path.exists(export_path):
                logger.error(f"[ERROR] Export file not found: {export_path}")
                logger.info("[INFO] Run --analyze first to export applications")
                return {}
            logger.info(f"[OK] Found existing export: {export_path}")

        # Get list of custom libraries from Striim
        logger.info("\nGetting list of custom libraries...")
        libraries_result = self.api.execute_command("LIST LIBRARIES;")
        custom_libraries = set()  # For backward compatibility (base names)
        library_files = {}  # Map base_name -> full filename

        if not libraries_result:
            logger.warning("[WARN] Failed to get libraries list")
        else:
            if isinstance(libraries_result, list) and len(libraries_result) > 0:
                output = libraries_result[0].get('output', [])
                for item in output:
                    if 'fileName' in item:
                        filename = item['fileName']
                        # Extract base name (e.g., AdvFormat from AdvFormat-5.0.2.jar)
                        base_name = filename.split('-')[0].split('.')[0]
                        custom_libraries.add(base_name)
                        library_files[base_name] = filename
            logger.info(f"[OK] Found {len(custom_libraries)} custom libraries:")
            for base_name, filename in sorted(library_files.items()):
                logger.info(f"     - {base_name}: {filename}")

        # Get application states from Striim
        logger.info("\nGetting application states...")
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
        logger.info(f"[OK] Retrieved {len(app_states)} application(s)")

        # Get deployment plans for deployed/running apps
        logger.info("\nGetting deployment plans...")
        deployment_plans = self._get_deployment_plans(app_states)
        logger.info(f"[OK] Retrieved {len(deployment_plans)} deployment plan(s)")

        # Analyze the existing export file or directory
        logger.info("\nAnalyzing TQL files...")
        passphrase = config.get_config().get('passphrase', 'striim123')

        if use_directory:
            components_found = self._analyze_directory_for_components(extracted_dir, custom_libraries)
        else:
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
                    drop_type=comp.get('component_type', 'SOURCE'),
                    flow=comp.get('flow'),
                    simple_name=comp.get('simple_name'),
                    udfs=comp.get('udfs')  # Pass UDFs for CQ components
                )

        # Save application states, deployment plans, and library files
        self.state.state['app_states'] = app_states
        self.state.state['deployment_plans'] = deployment_plans
        self.state.state['library_files'] = library_files

        self.state.set_phase('analyzed')

        # Display summary
        logger.info(f"\n[OK] Analysis complete. Found components in {len(components_found)} applications")
        for app_name, comps in components_found.items():
            logger.info(f"  {app_name}: {len(comps)} component(s)")
            for comp in comps:
                logger.info(f"    - {comp['type']}: {comp['name']}")

        # Display application states
        logger.info(f"\n[INFO] Application States:")
        for app_name, status in sorted(app_states.items()):
            marker = " *" if app_name in components_found else ""
            logger.info(f"  {app_name}: {status}{marker}")
        if components_found:
            logger.info("\n  * = Contains custom components")

        return components_found

    def analyze(self) -> Dict:
        """Analyze all applications to find OPs and UDFs"""
        if self.dry_run:
            logger.info("\n=== [DRY-RUN] Analyzing Applications ===")
        else:
            logger.info("\n=== Analyzing Applications ===")

        # Check if we have existing component data that would be overwritten
        if self.state.state['apps_with_components'] and not self.dry_run:
            num_apps = len(self.state.state['apps_with_components'])
            total_components = sum(len(comps) for comps in self.state.state['apps_with_components'].values())

            logger.warning("\n" + "="*70)
            logger.warning("⚠️  WARNING: EXISTING COMPONENT DATA WILL BE OVERWRITTEN!")
            logger.warning("="*70)
            logger.warning(f"Current state contains {total_components} component(s) across {num_apps} application(s):")
            for app_name, components in self.state.state['apps_with_components'].items():
                logger.warning(f"  • {app_name}: {len(components)} component(s)")
                for comp in components:
                    logger.warning(f"    - {comp['type']}: {comp['name']}")
            logger.warning("\nRe-analyzing will:")
            logger.warning("  1. Create a backup at: upgrade_state.json.backup")
            logger.warning("  2. REPLACE all component data with current app state")
            logger.warning("  3. If components were already removed, you will LOSE the CREATE statements!")
            logger.warning("\nThis means you will NOT be able to restore these components unless you")
            logger.warning("use the backup file!")
            logger.warning("="*70)

            response = input("\nAre you sure you want to continue? (yes/no): ").strip().lower()
            if response not in ['yes', 'y']:
                logger.info("[CANCELLED] Analysis cancelled by user")
                return {}

            # Create backup
            backup_file = f"{self.state.state_file}.backup"
            import shutil
            shutil.copy(self.state.state_file, backup_file)
            logger.info(f"\n[OK] Backed up existing state to {backup_file}")

        # Get all apps and their states using mon command
        result = self.api.execute_command("mon;")
        if not result:
            logger.error("[ERROR] Failed to get application list")
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

        logger.info(f"[OK] Retrieved {len(app_states)} application(s)")

        # Get deployment plans for deployed/running apps
        logger.info("\nGetting deployment plans...")
        deployment_plans = self._get_deployment_plans(app_states)
        logger.info(f"[OK] Retrieved {len(deployment_plans)} deployment plan(s)")

        # In dry-run mode, show states and exit early
        if self.dry_run:
            logger.info(f"\n[INFO] Application States:")
            for app_name, status in sorted(app_states.items()):
                plan = deployment_plans.get(app_name, {})
                strategy = plan.get('strategy', 'N/A')
                group = plan.get('deploymentGroup', 'N/A')
                logger.info(f"  {app_name}: {status} (Deploy: {strategy} in {group})")
            logger.info("\n[DRY-RUN] Would export applications and analyze TQL for custom components")
            return {}

        # Get list of custom libraries
        logger.info("\nGetting list of custom libraries...")
        libraries_result = self.api.execute_command("LIST LIBRARIES;")
        custom_libraries = set()  # For backward compatibility (base names)
        library_files = {}  # Map base_name -> full filename

        if not libraries_result:
            logger.warning("[WARN] Failed to get libraries list")
        else:
            if isinstance(libraries_result, list) and len(libraries_result) > 0:
                output = libraries_result[0].get('output', [])
                for item in output:
                    if 'fileName' in item:
                        filename = item['fileName']
                        # Extract base name (e.g., AdvFormat from AdvFormat-5.0.2.jar)
                        base_name = filename.split('-')[0].split('.')[0]
                        custom_libraries.add(base_name)
                        library_files[base_name] = filename
            logger.info(f"[OK] Found {len(custom_libraries)} custom libraries:")
            for base_name, filename in sorted(library_files.items()):
                logger.info(f"     - {base_name}: {filename}")

        # Export all to analyze TQL
        logger.info("\nExporting applications to analyze...")
        passphrase = config.get_config().get('passphrase', 'striim123')

        # Create backup directory if it doesn't exist
        Path(BACKUP_DIR).mkdir(exist_ok=True)
        export_path = os.path.join(BACKUP_DIR, "all_applications.zip")

        if not self.api.export_all_applications(export_path, passphrase):
            logger.error("[ERROR] Export failed")
            return {}

        logger.info(f"[OK] Exported to {export_path}")

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
                    drop_type=comp.get('component_type', 'SOURCE'),
                    flow=comp.get('flow'),
                    simple_name=comp.get('simple_name'),
                    udfs=comp.get('udfs')  # Pass UDFs for CQ components
                )

        # Save application states, deployment plans, and library files
        self.state.state['app_states'] = app_states
        self.state.state['deployment_plans'] = deployment_plans
        self.state.state['library_files'] = library_files

        self.state.set_phase('analyzed')

        # Display summary
        logger.info(f"\n[OK] Analysis complete. Found components in {len(components_found)} applications")
        for app_name, comps in components_found.items():
            logger.info(f"  {app_name}: {len(comps)} component(s)")
            for comp in comps:
                logger.info(f"    - {comp['type']}: {comp['name']}")

        # Display application states
        logger.info(f"\n[INFO] Application States:")
        for app_name, status in sorted(app_states.items()):
            # Highlight apps with custom components
            marker = " *" if app_name in components_found else ""
            logger.info(f"  {app_name}: {status}{marker}")
        if components_found:
            logger.info("\n  * = Contains custom components")

        return components_found

    def _analyze_directory_for_components(self, directory_path: str, custom_libraries: set) -> Dict:
        """Analyze TQL files from an extracted directory"""
        components = {}

        try:
            # Get all TQL files from directory
            tql_files = [f for f in os.listdir(directory_path) if f.endswith('.tql')]

            if not tql_files:
                logger.warning("[WARN] No TQL files found in directory")
                return {}

            logger.info(f"[OK] Found {len(tql_files)} TQL files in directory")

            # Analyze each TQL file
            for i, tql_file in enumerate(tql_files, 1):
                if i % 50 == 0:
                    logger.info(f"  Processed {i}/{len(tql_files)} files...")

                file_path = os.path.join(directory_path, tql_file)
                with open(file_path, 'r', encoding='utf-8') as f:
                    tql_content = f.read()
                    file_components = self._analyze_tql_for_components(tql_content, custom_libraries)

                    # Merge components from this file
                    for app_name, comps in file_components.items():
                        if app_name not in components:
                            components[app_name] = []
                        components[app_name].extend(comps)

            return components

        except Exception as e:
            logger.error(f"[ERROR] Failed to analyze directory: {e}")
            import traceback
            traceback.print_exc()
            return {}

    def _analyze_zip_for_components(self, zip_path: str, passphrase: str, custom_libraries: set) -> Dict:
        """Extract TQL files from zip and analyze for components"""
        components = {}

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # Extract all TQL files
                tql_files = [f for f in zip_ref.namelist() if f.endswith('.tql')]

                if not tql_files:
                    logger.warning("[WARN] No TQL files found in export")
                    return {}

                logger.info(f"[OK] Found {len(tql_files)} TQL files in export")

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
            logger.error(f"[ERROR] Failed to analyze zip file: {e}")
            return {}

    def _analyze_tql_for_components(self, tql_content: str, custom_libraries: set) -> Dict:
        """Parse TQL to find OPs and UDFs"""
        components = {}

        # Patterns for CREATE statements - match with optional namespace
        # Matches: CREATE SOURCE name USING or CREATE SOURCE namespace.name USING
        op_pattern = r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:OPEN\s+)?(?:SOURCE\s+|PROCESSOR\s+)(?:(\w+)\.)?(\w+)\s+USING\s+(\S+)'
        # UDF calls pattern: matches multi-part function calls like com.striim.util.AdvFormat.LowercaseTableName(...)
        # Must have at least 3 parts (package.class.method) to distinguish from Striim built-ins
        # First part must be lowercase (package), subsequent parts can be any case (class/method names)
        udf_call_pattern = r'\b([a-z][a-z0-9]*(?:\.[a-zA-Z][a-zA-Z0-9]*){2,})\s*\('
        app_pattern = r'CREATE\s+(?:OR\s+REPLACE\s+)?APPLICATION\s+(?:(\w+)\.)?(\w+)'

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
            # Check for the specific "OPEN SOURCE" or "OPEN PROCESSOR" keyword sequence
            # (not just substring 'OPEN' which could match component/adapter names)
            match_text = match.group(0).upper()
            is_open_processor = bool(re.search(r'\bOPEN\s+(?:SOURCE|PROCESSOR)\b', match_text))
            component_type = 'OPEN PROCESSOR' if is_open_processor else 'SOURCE'

            # Try to find which app this belongs to (pass known position to avoid re-searching)
            app_name = self._find_app_for_component(tql_content, namespace, name, apps_found, match.start())
            if app_name:
                # Find which flow (if any) this component belongs to
                flow_name = self._find_flow_for_component(tql_content, match.start())

                if app_name not in components:
                    components[app_name] = []
                components[app_name].append({
                    'type': 'OP',
                    'name': full_name,
                    'simple_name': simple_name,  # For DROP command
                    'component_type': component_type,  # For DROP command (SOURCE or OPEN PROCESSOR)
                    'adapter': adapter,
                    'flow': flow_name,  # Flow name if inside a FLOW block, None otherwise
                    'create_statement': self._extract_full_statement(tql_content, match.start())
                })

        # Find all UDF calls within CQ statements
        # UDFs are Java functions called within CQs, not created with CREATE FUNCTION
        # Pattern: com.package.class.method(...) - must have at least 3 parts
        # We track ALL UDFs that match this pattern, regardless of custom libraries list
        udf_calls_found = set()
        for match in re.finditer(udf_call_pattern, tql_content, re.IGNORECASE):
            udf_full_name = match.group(1)

            # Extract the package/class prefix (e.g., "com.striim.util" from "com.striim.util.AdvFormat")
            # Must have at least 3 parts (package.class.method) to distinguish from Striim built-ins
            parts = udf_full_name.split('.')
            if len(parts) < 3:
                continue

            # Track all UDFs that match the pattern
            # Note: We don't filter by custom_libraries because that list may be incomplete
            udf_calls_found.add(udf_full_name)

        # For each UDF call found, determine which CQ(s) and app(s) it belongs to
        for udf_name in udf_calls_found:
            # Find ALL CQs that contain this UDF call
            cq_infos = self._find_cq_for_udf(tql_content, udf_name, apps_found)

            # Process each CQ found
            for cq_info in cq_infos:
                app_name, cq_name, cq_statement, cq_position = cq_info
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
                    # Use the exact CQ position returned by _find_cq_for_udf
                    # instead of re-searching (which could find a different match)
                    flow_name = self._find_flow_for_component(tql_content, cq_position)

                    # Create new CQ entry
                    components[app_name].append({
                        'type': 'CQ',
                        'name': cq_name,
                        'simple_name': cq_name.split('.')[-1],  # For DROP command
                        'component_type': 'CQ',
                        'flow': flow_name,  # Add flow information
                        'udfs': [udf_name],
                        'create_statement': cq_statement
                    })

        return components

    def _extract_full_statement(self, tql: str, start_pos: int) -> str:
        """Extract full CREATE statement ending with semicolon

        Properly handles nested parentheses in CREATE SOURCE/OP statements.
        The statement structure is:
        CREATE [OR REPLACE] SOURCE name USING library ( properties ) OUTPUT TO target;

        We need to find the matching closing ) for the properties, then find the ; after that.

        For CQ statements (which may have no parentheses in their body), we must check
        if a semicolon appears before the first parenthesis — if so, the statement ends
        at that semicolon rather than extending into the next statement's parentheses.
        """
        # Find the opening parenthesis for the properties section
        paren_start = tql.find('(', start_pos)
        semi_pos = tql.find(';', start_pos)

        if paren_start == -1 or (semi_pos != -1 and semi_pos < paren_start):
            # No parentheses, OR semicolon comes before the first parenthesis
            # (e.g., CQ without function calls: CREATE CQ x INSERT INTO y SELECT col FROM z;)
            # The statement ends at the semicolon
            if semi_pos == -1:
                semi_pos = len(tql)
            return tql[start_pos:semi_pos+1].strip()

        # Count parentheses to find the matching closing one
        paren_count = 0
        pos = paren_start
        paren_end = -1

        while pos < len(tql):
            if tql[pos] == '(':
                paren_count += 1
            elif tql[pos] == ')':
                paren_count -= 1
                if paren_count == 0:
                    paren_end = pos
                    break
            pos += 1

        if paren_end == -1:
            # Couldn't find matching closing paren, fall back to semicolon
            end_pos = tql.find(';', start_pos)
            if end_pos == -1:
                end_pos = len(tql)
            return tql[start_pos:end_pos+1].strip()

        # Now find the semicolon after the closing parenthesis
        end_pos = tql.find(';', paren_end)
        if end_pos == -1:
            end_pos = len(tql)

        return tql[start_pos:end_pos+1].strip()

    def _get_deployment_plans(self, app_states: Dict[str, str]) -> Dict[str, Dict]:
        """Get deployment plans for deployed/running applications

        Handles both single-flow and multi-flow applications.

        Returns a dict mapping app_name to deployment plan:
        {
            'admin.MyApp': {
                'application': {
                    'strategy': 'ON_ONE',
                    'deploymentGroup': 'default'
                },
                'flows': {
                    'Flow1': {
                        'strategy': 'ON_ALL',
                        'deploymentGroup': 'agent'
                    },
                    'Flow2': {
                        'strategy': 'ON_ONE',
                        'deploymentGroup': 'default'
                    }
                }
            }
        }
        """
        deployment_plans = {}

        for app_name, status in app_states.items():
            # Only get deployment plans for deployed or running apps
            if status not in ['DEPLOYED', 'RUNNING']:
                continue

            # Execute DESCRIBE command
            result = self.api.execute_command(f"DESCRIBE {app_name};")
            if not result or not isinstance(result, list) or len(result) == 0:
                continue

            # Extract deployment plan from response
            output = result[0].get('output', [])
            if not output or len(output) == 0:
                continue

            app_info = output[0]
            deployment_plan_raw = app_info.get('deploymentPlan')

            if not deployment_plan_raw:
                continue

            # Handle both single-flow (dict) and multi-flow (list) cases
            plan_data = {
                'application': {},
                'flows': {}
            }

            if isinstance(deployment_plan_raw, dict):
                # Single-flow app: deploymentPlan is a dict
                plan_data['application'] = {
                    'strategy': deployment_plan_raw.get('strategy', 'ON_ONE'),
                    'deploymentGroup': deployment_plan_raw.get('deploymentGroup', 'default')
                }
            elif isinstance(deployment_plan_raw, list):
                # Multi-flow app: deploymentPlan is a list
                for plan in deployment_plan_raw:
                    flow_type = plan.get('flowType')
                    flow_name = plan.get('flowName')
                    strategy = plan.get('strategy', 'ON_ONE')
                    deployment_group = plan.get('deploymentGroup', 'default')

                    if flow_type == 'APPLICATION':
                        plan_data['application'] = {
                            'strategy': strategy,
                            'deploymentGroup': deployment_group
                        }
                    elif flow_type == 'FLOW':
                        plan_data['flows'][flow_name] = {
                            'strategy': strategy,
                            'deploymentGroup': deployment_group
                        }

            if plan_data['application']:
                deployment_plans[app_name] = plan_data

        return deployment_plans

    def _find_cq_for_udf(self, tql: str, udf_name: str, apps_found: Set[str]) -> List[Tuple[str, str, str, int]]:
        """Find ALL CQs that contain a UDF call and which app they belong to
        Returns: List of (app_name, cq_name, cq_statement, cq_position) tuples
        The cq_position is the character offset of the CREATE CQ in the TQL, used for
        accurate flow detection without needing to re-search.
        """
        # Pattern to find CQ statements
        # Match CQ body up to single semicolon (not double)
        cq_pattern = r'CREATE\s+(?:OR\s+REPLACE\s+)?CQ\s+(?:(\w+)\.)?(\w+)\s+(.*?);'

        cqs_found = []
        for match in re.finditer(cq_pattern, tql, re.IGNORECASE | re.DOTALL):
            namespace, cq_name, cq_body = match.groups()

            # Check if this CQ contains the UDF call
            if udf_name in cq_body:
                # Find which app this CQ belongs to
                full_cq_name = f"{namespace}.{cq_name}" if namespace else cq_name
                app_name = self._find_app_for_component(tql, namespace, cq_name, apps_found, match.start())

                if app_name:
                    # Extract the full CQ statement
                    cq_statement = self._extract_full_statement(tql, match.start())
                    cqs_found.append((app_name, full_cq_name, cq_statement, match.start()))

        return cqs_found

    def _find_flow_for_component(self, tql: str, comp_position: int) -> Optional[str]:
        """Find which FLOW (if any) a component belongs to

        Returns the flow name if the component is inside a CREATE FLOW ... END FLOW block,
        otherwise returns None (component is at application level)
        """
        # Find all FLOW blocks
        flow_pattern = r'CREATE\s+FLOW\s+(\w+)\s*;?(.*?)END\s+FLOW\s+\1\s*;?'

        for match in re.finditer(flow_pattern, tql, re.IGNORECASE | re.DOTALL):
            flow_name = match.group(1)
            flow_start = match.start()
            flow_end = match.end()

            # Check if component is within this flow block
            if flow_start < comp_position < flow_end:
                return flow_name

        return None

    def _find_app_for_component(self, tql: str, namespace: Optional[str], comp_name: str, apps_found: Set[str], comp_pos: int = -1) -> Optional[str]:
        """Find which application a component belongs to

        Args:
            tql: Full TQL content
            namespace: Component namespace (if any)
            comp_name: Component name
            apps_found: Set of known app names (unused, kept for API compatibility)
            comp_pos: Known character position of the component in the TQL.
                      If provided (>= 0), uses this directly instead of re-searching.
                      This avoids finding the wrong match when a name appears multiple times.
        """
        # Use the known position if provided, otherwise search for the component
        if comp_pos < 0:
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
        logger.info("\n=== Removing Components from Applications ===")

        if not self.state.state['apps_with_components']:
            logger.warning("[WARN] No components found. Run --analyze first.")
            return

        for app_name, components in self.state.state['apps_with_components'].items():
            logger.info(f"\nProcessing {app_name}...")

            # Check app state - if RUNNING, need to STOP first, then UNDEPLOY
            app_state = self.state.state.get('app_states', {}).get(app_name, 'UNKNOWN')

            # Handle transitional states - these should not be processed
            if app_state in ['STARTING', 'STOPPING', 'DEPLOYING', 'UNDEPLOYING']:
                logger.error(f"  [ERROR] App {app_name} is in transitional state: {app_state}")
                logger.error(f"  [ERROR] Please wait for app to reach stable state before running upgrade")
                continue

            if app_state == 'RUNNING':
                if self.dry_run:
                    logger.info(f"  [DRY-RUN] Would stop {app_name} (currently RUNNING)")
                else:
                    logger.info(f"  Stopping {app_name}...")
                    self.api.execute_command(f"STOP APPLICATION {app_name};")

            # Undeploy app (whether it was RUNNING, DEPLOYED, HALTED, or TERMINATED)
            # HALTED and TERMINATED apps must also be undeployed before components can be removed
            if app_state in ['RUNNING', 'DEPLOYED', 'HALTED', 'TERMINATED']:
                if self.dry_run:
                    logger.info(f"  [DRY-RUN] Would undeploy {app_name} (currently {app_state})")
                else:
                    logger.info(f"  Undeploying {app_name} (currently {app_state})...")
                    self.api.execute_command(f"UNDEPLOY APPLICATION {app_name};")
            elif app_state in ['UNKNOWN', 'NOT_FOUND']:
                # App exists as TQL file but is not deployed in Striim
                # We can still modify the TQL via ALTER commands
                logger.info(f"  [INFO] App {app_name} is not currently deployed (state: {app_state})")
                logger.info(f"  [INFO] Will modify TQL file only (no UNDEPLOY needed)")

            for comp in components:
                comp_name = comp['name']
                comp_type = comp['type']
                component_type = comp.get('component_type', 'SOURCE')  # SOURCE or OPEN PROCESSOR
                flow_name = comp.get('flow')  # Flow name if inside a FLOW block

                if self.dry_run:
                    flow_info = f" (in FLOW {flow_name})" if flow_name else ""
                    logger.info(f"  [DRY-RUN] Would remove {comp_type} {comp_name}{flow_info}")
                    continue

                # Send ALTER, DROP, RECOMPILE as a single batch command
                flow_info = f" from FLOW {flow_name}" if flow_name else ""
                logger.info(f"  Removing {comp_type} {comp_name}{flow_info}...")

                # DROP command needs the component type (SOURCE or OPEN PROCESSOR)
                drop_cmd = f"DROP {component_type} {comp_name};"

                # If component is in a FLOW, wrap in ALTER FLOW ... END FLOW
                if flow_name:
                    batch_cmd = f"ALTER APPLICATION {app_name};\nALTER FLOW {flow_name};\n{drop_cmd}\nEND FLOW {flow_name};\nALTER APPLICATION {app_name} RECOMPILE;"
                else:
                    batch_cmd = f"ALTER APPLICATION {app_name};\n{drop_cmd}\nALTER APPLICATION {app_name} RECOMPILE;"

                # Print the batch command for debugging
                logger.debug(f"    [CMD] Executing batch command:")
                for line in batch_cmd.split('\n'):
                    logger.debug(f"          {line}")

                result = self.api.execute_command(batch_cmd)

                if self._is_command_failure(result):
                    logger.error(f"    [ERROR] {self._get_failure_message(result)}")
                    logger.error(f"  [ERROR] Failed to remove {comp_name}")
                else:
                    self.state.state['removed_components'].setdefault(app_name, []).append(comp_name)
                    logger.info(f"  [OK] Removed {comp_name}")

        if not self.dry_run:
            self.state.set_phase('components_removed')
        logger.info("\n[OK] All components removed from applications")

    def unload_components(self):
        """Unload OPs/UDFs from Striim

        This unloads ALL LIBRARIES from LIST LIBRARIES.
        For each library, tries UNLOAD first (for UDFs), then UNLOAD OPEN PROCESSOR (for OPs).

        Example:
            UNLOAD 'UploadedFiles/AdvFormat-5.0.2.jar';
            UNLOAD OPEN PROCESSOR 'UploadedFiles/EventChanger-5.0.2.jar';
        """
        logger.info("\n=== Unloading Components from Striim ===")

        # Get library files from state (from LIST LIBRARIES)
        library_files = self.state.state.get('library_files', {})
        if not library_files:
            logger.error("[ERROR] No library files found in state. Run --analyze first.")
            return

        logger.info(f"\n[INFO] Unloading ALL libraries from LIST LIBRARIES ({len(library_files)}):")
        for base_name, filename in sorted(library_files.items()):
            logger.info(f"  - {base_name}: {filename}")

        # Unload each library
        # Try UNLOAD first (for UDFs), then UNLOAD OPEN PROCESSOR (for OPs)
        unloaded_count = 0
        failed_count = 0

        for base_name, filename in sorted(library_files.items()):
            file_path = f"UploadedFiles/{filename}"

            if self.dry_run:
                logger.info(f"\n  [DRY-RUN] Would unload '{file_path}'")
                continue

            logger.info(f"\n  Unloading '{file_path}'...")

            # Try UNLOAD first (for UDFs)
            unload_cmd = f"UNLOAD '{file_path}';"
            result = self.api.execute_command(unload_cmd)

            if result and not (isinstance(result, list) and len(result) > 0 and result[0].get('failureMessage')):
                self.state.state['unloaded_components'].append(file_path)
                logger.info(f"    [OK] Unloaded {filename} (UDF)")
                unloaded_count += 1
            else:
                # Try UNLOAD OPEN PROCESSOR (for OPs)
                logger.info(f"    Trying as Open Processor...")
                unload_cmd = f"UNLOAD OPEN PROCESSOR '{file_path}';"
                result = self.api.execute_command(unload_cmd)

                if result and not (isinstance(result, list) and len(result) > 0 and result[0].get('failureMessage')):
                    self.state.state['unloaded_components'].append(file_path)
                    logger.info(f"    [OK] Unloaded {filename} (OP)")
                    unloaded_count += 1
                else:
                    error_msg = result[0].get('failureMessage', 'Unknown error') if isinstance(result, list) and len(result) > 0 else 'Unknown error'
                    logger.error(f"    [ERROR] Failed to unload {filename}: {error_msg}")
                    failed_count += 1

        if not self.dry_run:
            self.state.set_phase('components_unloaded')

        logger.info(f"\n[OK] Unload complete: {unloaded_count} succeeded, {failed_count} failed (total: {len(library_files)})")

    def load_components(self, component_path: str = None):
        """Load new OPs/UDFs after upgrade

        Can load multiple files from UploadedFiles/ directory.
        For each file, tries LOAD first (for UDFs), then LOAD OPEN PROCESSOR (for OPs).
        """
        logger.info("\n=== Loading Components ===")

        if component_path:
            # Manual mode - load specific file(s)
            # Support comma-separated list of files
            files_to_load = [f.strip() for f in component_path.split(',')]

            logger.info(f"\n[INFO] Loading {len(files_to_load)} file(s)...")
            loaded_count = 0
            failed_count = 0

            for file_path in files_to_load:
                # Ensure path starts with UploadedFiles/
                if not file_path.startswith('UploadedFiles/'):
                    file_path = f"UploadedFiles/{file_path}"

                if self.dry_run:
                    logger.info(f"\n  [DRY-RUN] Would load {file_path}")
                    continue

                logger.info(f"\n  Loading '{file_path}'...")

                # Try LOAD first (for UDFs)
                load_cmd = f"LOAD '{file_path}';"
                result = self.api.execute_command(load_cmd)

                if result and not (isinstance(result, list) and len(result) > 0 and result[0].get('failureMessage')):
                    self.state.state['loaded_components'].append(file_path)
                    logger.info(f"    [OK] Loaded {file_path} (UDF)")
                    loaded_count += 1
                else:
                    # Try LOAD OPEN PROCESSOR (for OPs)
                    logger.info(f"    Trying as Open Processor...")
                    load_cmd = f"LOAD OPEN PROCESSOR '{file_path}';"
                    result = self.api.execute_command(load_cmd)

                    if result and not (isinstance(result, list) and len(result) > 0 and result[0].get('failureMessage')):
                        self.state.state['loaded_components'].append(file_path)
                        logger.info(f"    [OK] Loaded {file_path} (OP)")
                        loaded_count += 1
                    else:
                        error_msg = result[0].get('failureMessage', 'Unknown error') if isinstance(result, list) and len(result) > 0 else 'Unknown error'
                        logger.error(f"    [ERROR] Failed to load {file_path}: {error_msg}")
                        failed_count += 1

            if not self.dry_run:
                self.state.save()
                logger.info(f"\n[OK] Load complete: {loaded_count} succeeded, {failed_count} failed (total: {len(files_to_load)})")
        else:
            # Interactive mode - map old components to new versions
            self._interactive_component_loading()

    def _interactive_component_loading(self):
        """Interactive mode to map old component versions to new ones"""
        # Get list of components that were unloaded
        if not self.state.state.get('unloaded_components'):
            logger.warning("[WARN] No components were unloaded. Nothing to load.")
            logger.info("[INFO] If you want to load a specific component, use:")
            logger.info("       --load-components --component-path UploadedFiles/YourComponent.jar")
            return

        unloaded = self.state.state['unloaded_components']
        logger.info(f"\n[INFO] Found {len(unloaded)} component(s) that were unloaded:")
        for comp in unloaded:
            logger.info(f"  - {comp}")

        # Get list of available files in UploadedFiles/
        logger.info("\n[INFO] Fetching available files from UploadedFiles/...")
        available_files = self._get_uploaded_files()

        if not available_files:
            logger.error("[ERROR] No files found in UploadedFiles/")
            logger.info("[INFO] Please upload your new component files via Striim UI first")
            return

        logger.info(f"[OK] Found {len(available_files)} file(s) in UploadedFiles/:")
        for f in available_files:
            logger.info(f"  - {f}")

        # Build mapping with auto-suggestions
        logger.info("\n=== Component Mapping ===")
        logger.info("Mapping old components to new versions...\n")

        component_mapping = {}
        for old_comp in unloaded:
            # Extract base name (e.g., "StriimWatcher" from "StriimWatcher-5.2.0.5.jar")
            base_name = self._extract_base_name(old_comp)

            # Find matching files
            matches = [f for f in available_files if base_name.lower() in f.lower()]

            if len(matches) == 1:
                # Auto-match if only one candidate
                suggested = matches[0]
                logger.info(f"Old: {old_comp}")
                logger.info(f"New: {suggested} (auto-matched)")

                if not self.dry_run:
                    confirm = input("Accept this mapping? (yes/no/manual): ").strip().lower()
                    if confirm == 'yes' or confirm == 'y':
                        component_mapping[old_comp] = f"UploadedFiles/{suggested}"
                    elif confirm == 'manual' or confirm == 'm':
                        component_mapping[old_comp] = self._manual_file_selection(old_comp, available_files)
                    else:
                        logger.info(f"  [SKIP] Skipping {old_comp}")
                else:
                    component_mapping[old_comp] = f"UploadedFiles/{suggested}"

            elif len(matches) > 1:
                # Multiple matches - let user choose
                logger.info(f"Old: {old_comp}")
                logger.info(f"Found {len(matches)} possible matches:")
                for i, match in enumerate(matches, 1):
                    logger.info(f"  {i}. {match}")

                if not self.dry_run:
                    choice = input(f"Select 1-{len(matches)} or 's' to skip: ").strip()
                    if choice.lower() == 's':
                        logger.info(f"  [SKIP] Skipping {old_comp}")
                    elif choice.isdigit() and 1 <= int(choice) <= len(matches):
                        selected = matches[int(choice) - 1]
                        component_mapping[old_comp] = f"UploadedFiles/{selected}"
                        logger.info(f"  [OK] Mapped to {selected}")
                    else:
                        logger.info(f"  [SKIP] Invalid choice, skipping {old_comp}")
                else:
                    # In dry-run, use first match
                    component_mapping[old_comp] = f"UploadedFiles/{matches[0]}"
            else:
                # No auto-match found
                logger.info(f"Old: {old_comp}")
                logger.info(f"No automatic match found for base name '{base_name}'")

                if not self.dry_run:
                    component_mapping[old_comp] = self._manual_file_selection(old_comp, available_files)
                else:
                    logger.info(f"  [SKIP] Would prompt for manual selection")

            logger.info("")  # Blank line between components

        # Show final mapping
        if component_mapping:
            logger.info("\n=== Final Component Mapping ===")
            for old, new in component_mapping.items():
                logger.info(f"  {old} -> {new}")

            # Load all components
            if self.dry_run:
                logger.info("\n[DRY-RUN] Would load the above components")
            else:
                logger.info("\n[INFO] Loading components...")
                loaded_count = 0
                failed_count = 0

                for old_comp, new_path in component_mapping.items():
                    logger.info(f"\nLoading {new_path}...")

                    # Try LOAD first (for UDFs)
                    load_cmd = f"LOAD '{new_path}';"
                    result = self.api.execute_command(load_cmd)

                    if result and not (isinstance(result, list) and len(result) > 0 and result[0].get('failureMessage')):
                        self.state.state['loaded_components'].append(new_path)
                        logger.info(f"  [OK] Loaded {new_path} (UDF)")
                        loaded_count += 1
                    else:
                        # Try LOAD OPEN PROCESSOR (for OPs)
                        logger.info(f"  Trying as Open Processor...")
                        load_cmd = f"LOAD OPEN PROCESSOR '{new_path}';"
                        result = self.api.execute_command(load_cmd)

                        if result and not (isinstance(result, list) and len(result) > 0 and result[0].get('failureMessage')):
                            self.state.state['loaded_components'].append(new_path)
                            logger.info(f"  [OK] Loaded {new_path} (OP)")
                            loaded_count += 1
                        else:
                            error_msg = result[0].get('failureMessage', 'Unknown error') if isinstance(result, list) and len(result) > 0 else 'Unknown error'
                            logger.error(f"  [ERROR] Failed to load {new_path}: {error_msg}")
                            failed_count += 1

                self.state.save()
                logger.info(f"\n[OK] Component loading complete: {loaded_count} succeeded, {failed_count} failed")
        else:
            logger.warning("[WARN] No components were mapped")

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
        logger.info(f"Available files:")
        for i, f in enumerate(available_files, 1):
            logger.info(f"  {i}. {f}")

        choice = input(f"Select 1-{len(available_files)} or 's' to skip: ").strip()
        if choice.lower() == 's':
            logger.info(f"  [SKIP] Skipping {old_comp}")
            return None
        elif choice.isdigit() and 1 <= int(choice) <= len(available_files):
            selected = available_files[int(choice) - 1]
            logger.info(f"  [OK] Mapped to {selected}")
            return f"UploadedFiles/{selected}"
        else:
            logger.info(f"  [SKIP] Invalid choice, skipping {old_comp}")
            return None

    def _make_create_or_replace(self, create_stmt: str) -> str:
        """Ensure a CREATE statement uses CREATE OR REPLACE to handle existing components"""
        stmt_upper = create_stmt.strip().upper()
        if stmt_upper.startswith('CREATE OR REPLACE'):
            return create_stmt  # Already has OR REPLACE
        if stmt_upper.startswith('CREATE SOURCE'):
            return create_stmt.replace('CREATE SOURCE', 'CREATE OR REPLACE SOURCE', 1)
        elif stmt_upper.startswith('CREATE OPEN PROCESSOR'):
            return create_stmt.replace('CREATE OPEN PROCESSOR', 'CREATE OR REPLACE OPEN PROCESSOR', 1)
        elif stmt_upper.startswith('CREATE CQ'):
            return create_stmt.replace('CREATE CQ', 'CREATE OR REPLACE CQ', 1)
        return create_stmt

    def _is_command_failure(self, result) -> bool:
        """Check if a Striim API result indicates a failure.

        Striim can return HTTP 200 with a failureMessage in the JSON body.
        Returns True if the result contains a failure, False if success.
        """
        if not result:
            return True
        if isinstance(result, list) and len(result) > 0 and result[0].get('failureMessage'):
            return True
        return False

    def _get_failure_message(self, result) -> str:
        """Extract failure message from a Striim API result."""
        if not result:
            return 'No response from API'
        if isinstance(result, list) and len(result) > 0:
            return result[0].get('failureMessage', 'Unknown error')
        return 'Unknown error'

    def _build_deploy_cmd(self, app_name: str, deployment_plans: Dict) -> Tuple[str, str, str, Dict]:
        """Build a DEPLOY command for an application using its deployment plan.

        Returns: (deploy_cmd, deploy_mode, group, flows)
        """
        plan = deployment_plans.get(app_name, {})
        app_plan = plan.get('application', {})
        flows = plan.get('flows', {})

        strategy = app_plan.get('strategy', 'ON_ONE')
        group = app_plan.get('deploymentGroup', 'default')

        # Extract namespace from app_name (e.g., "DATALAKE.myapp" -> "DATALAKE")
        app_parts = app_name.split('.')
        namespace = '.'.join(app_parts[:-1]) if len(app_parts) > 1 else ''

        # Convert strategy to command format (ON_ONE -> ONE, ON_ALL -> ALL)
        deploy_mode = strategy.replace('ON_', '')

        # Build DEPLOY command with per-flow deployment groups
        deploy_cmd = f"DEPLOY APPLICATION {app_name} ON {deploy_mode} IN {group}"

        if flows:
            # Add WITH clause for each flow (with namespace prefix)
            with_clauses = []
            for flow_name, flow_plan in flows.items():
                flow_strategy = flow_plan['strategy'].replace('ON_', '')
                flow_group = flow_plan['deploymentGroup']
                qualified_flow_name = f"{namespace}.{flow_name}" if namespace else flow_name
                with_clauses.append(f"{qualified_flow_name} ON {flow_strategy} IN {flow_group}")
            deploy_cmd += " WITH " + ", ".join(with_clauses)

        deploy_cmd += ";"
        return deploy_cmd, deploy_mode, group, flows

    def _log_deploy_plan(self, app_name: str, deploy_mode: str, group: str, flows: Dict, action: str = "Deploying"):
        """Log the deployment plan details for an application."""
        # Extract namespace for qualified flow names
        app_parts = app_name.split('.')
        namespace = '.'.join(app_parts[:-1]) if len(app_parts) > 1 else ''

        logger.info(f"\n{action} {app_name}...")
        if flows:
            logger.info(f"  App: {deploy_mode} in {group}")
            for flow_name, flow_plan in flows.items():
                qualified_flow_name = f"{namespace}.{flow_name}" if namespace else flow_name
                logger.info(f"  {qualified_flow_name}: {flow_plan['strategy'].replace('ON_', '')} in {flow_plan['deploymentGroup']}")
        else:
            logger.info(f"  {deploy_mode} in {group}")

    def _log_deploy_preview(self, label: str, app_names: List[str], deployment_plans: Dict):
        """Log a preview of apps to be deployed/started."""
        if not app_names:
            return
        logger.info(f"\n{label} ({len(app_names)}):")
        for app_name in app_names:
            _, _, group, flows = self._build_deploy_cmd(app_name, deployment_plans)
            # Extract namespace for qualified flow names
            app_parts = app_name.split('.')
            namespace = '.'.join(app_parts[:-1]) if len(app_parts) > 1 else ''
            strategy = deployment_plans.get(app_name, {}).get('application', {}).get('strategy', 'ON_ONE')
            logger.info(f"  - {app_name} ({strategy} in {group})")
            if flows:
                for flow_name, flow_plan in flows.items():
                    qualified_flow_name = f"{namespace}.{flow_name}" if namespace else flow_name
                    logger.info(f"      WITH {qualified_flow_name} ({flow_plan['strategy']} in {flow_plan['deploymentGroup']})")

    def _deploy_and_start_apps(self, apps_to_deploy: List[str], apps_to_start: List[str], deployment_plans: Dict):
        """Deploy and optionally start applications using their deployment plans.

        Args:
            apps_to_deploy: Apps to deploy only (DEPLOYED state)
            apps_to_start: Apps to deploy AND start (RUNNING state)
            deployment_plans: Deployment plan data from analyze phase
        """
        # Deploy-only applications
        for app_name in apps_to_deploy:
            deploy_cmd, deploy_mode, group, flows = self._build_deploy_cmd(app_name, deployment_plans)
            self._log_deploy_plan(app_name, deploy_mode, group, flows, "Deploying")

            result = self.api.execute_command(deploy_cmd)
            if self._is_command_failure(result):
                logger.error(f"  [ERROR] Failed to deploy {app_name}: {self._get_failure_message(result)}")
            else:
                logger.info(f"  [OK] Deployed {app_name}")

        # Deploy-and-start applications
        for app_name in apps_to_start:
            deploy_cmd, deploy_mode, group, flows = self._build_deploy_cmd(app_name, deployment_plans)
            self._log_deploy_plan(app_name, deploy_mode, group, flows, "Deploying and starting")

            # Deploy first
            result = self.api.execute_command(deploy_cmd)
            if self._is_command_failure(result):
                logger.error(f"  [ERROR] Failed to deploy {app_name}: {self._get_failure_message(result)}")
                continue
            logger.info(f"  [OK] Deployed {app_name}")

            # Then start
            result = self.api.execute_command(f"START APPLICATION {app_name};")
            if self._is_command_failure(result):
                logger.error(f"  [ERROR] Failed to start {app_name}: {self._get_failure_message(result)}")
            else:
                logger.info(f"  [OK] Started {app_name}")

    def _classify_app_states(self, app_states_iter) -> Tuple[List[str], List[str]]:
        """Classify apps into deploy-only and deploy-and-start lists based on original state.

        Args:
            app_states_iter: Iterable of (app_name, original_state) tuples

        Returns: (apps_to_deploy, apps_to_start)
        """
        apps_to_deploy = []
        apps_to_start = []

        for app_name, original_state in app_states_iter:
            if original_state == 'DEPLOYED':
                apps_to_deploy.append(app_name)
            elif original_state == 'RUNNING':
                apps_to_start.append(app_name)
            elif original_state in ['HALTED', 'TERMINATED']:
                # HALTED/TERMINATED apps should be restored to DEPLOYED state
                # (they were undeployed during component removal)
                apps_to_deploy.append(app_name)
                logger.info(f"[INFO] {app_name} was {original_state}, will restore to DEPLOYED")
            elif original_state == 'CREATED':
                # Already in correct state, no action needed
                pass
            else:
                logger.warning(f"[WARN] Unknown state '{original_state}' for {app_name}, skipping")

        return apps_to_deploy, apps_to_start

    def restore_to_apps(self):
        """Restore OPs/UDFs/CQs to applications using batched commands.

        Groups all components for each application into a single batch command,
        preserving flow structure:
          ALTER APPLICATION <name>;
          <app-level components>
          ALTER FLOW <flow1>;
          <flow1 components>
          END FLOW <flow1>;
          ALTER FLOW <flow2>;
          <flow2 components>
          END FLOW <flow2>;
          ALTER APPLICATION <name> RECOMPILE;
        """
        logger.info("\n=== Restoring Components to Applications ===")

        if not self.state.state['apps_with_components']:
            logger.warning("[WARN] No components to restore. Run --analyze first.")
            return

        for app_name, components in self.state.state['apps_with_components'].items():
            logger.info(f"\nProcessing {app_name}...")

            # Check if app is deployed/running - if so, need to undeploy first
            app_state = self.state.state.get('app_states', {}).get(app_name, 'UNKNOWN')

            if app_state in ['RUNNING', 'DEPLOYED', 'HALTED', 'TERMINATED']:
                if self.dry_run:
                    logger.info(f"  [DRY-RUN] Would undeploy {app_name} (currently {app_state})")
                else:
                    # Stop if running
                    if app_state == 'RUNNING':
                        logger.info(f"  Stopping {app_name}...")
                        self.api.execute_command(f"STOP APPLICATION {app_name};")

                    # Undeploy
                    logger.info(f"  Undeploying {app_name} (currently {app_state})...")
                    self.api.execute_command(f"UNDEPLOY APPLICATION {app_name};")

            # Group components by flow: None = app-level, flow_name = in that flow
            app_level_components = []
            flow_components = {}  # flow_name -> [components]

            for comp in components:
                flow_name = comp.get('flow')
                if flow_name:
                    if flow_name not in flow_components:
                        flow_components[flow_name] = []
                    flow_components[flow_name].append(comp)
                else:
                    app_level_components.append(comp)

            # Display what will be restored
            logger.info(f"  Components to restore:")
            if app_level_components:
                logger.info(f"    App-level: {len(app_level_components)} component(s)")
                for comp in app_level_components:
                    logger.info(f"      - {comp['type']}: {comp['name']}")
            for flow_name, flow_comps in flow_components.items():
                logger.info(f"    Flow '{flow_name}': {len(flow_comps)} component(s)")
                for comp in flow_comps:
                    logger.info(f"      - {comp['type']}: {comp['name']}")

            if self.dry_run:
                logger.info(f"  [DRY-RUN] Would restore all components in a single batch command")
                continue

            # Build single batch command for ALL components in this app
            batch_lines = []

            # 1. ALTER APPLICATION
            batch_lines.append(f"ALTER APPLICATION {app_name};")

            # 2. App-level components (outside any flow)
            for comp in app_level_components:
                create_stmt = self._make_create_or_replace(comp['create_statement'])
                batch_lines.append(create_stmt)

            # 3. Flow-grouped components
            for flow_name, flow_comps in flow_components.items():
                batch_lines.append(f"ALTER FLOW {flow_name};")
                for comp in flow_comps:
                    create_stmt = self._make_create_or_replace(comp['create_statement'])
                    batch_lines.append(create_stmt)
                batch_lines.append(f"END FLOW {flow_name};")

            # 4. RECOMPILE
            batch_lines.append(f"ALTER APPLICATION {app_name} RECOMPILE;")

            batch_cmd = "\n".join(batch_lines)

            # Print the batch command for debugging
            logger.debug(f"  [CMD] Executing batch command:")
            for line in batch_cmd.split('\n'):
                logger.debug(f"        {line}")

            # Execute as single batch
            result = self.api.execute_command(batch_cmd)

            if self._is_command_failure(result):
                logger.error(f"  [ERROR] {self._get_failure_message(result)}")
                logger.error(f"  [ERROR] Failed to restore components to {app_name}")
            else:
                self.state.state['restored_apps'].append(app_name)
                total_restored = len(app_level_components) + sum(len(fc) for fc in flow_components.values())
                logger.info(f"  [OK] Restored {total_restored} component(s) to {app_name}")

        if not self.dry_run:
            self.state.set_phase('components_restored')
        logger.info("\n[OK] All components restored to applications")

        # Automatically restore app states (DEPLOY/START) after components are restored
        logger.info("\n" + "="*60)
        logger.info("DEPLOYMENT PHASE")
        logger.info("="*60)
        logger.info("\n[INFO] Now restoring application states (DEPLOY/START)...")
        logger.info("[INFO] This will deploy/start all apps that were previously RUNNING/DEPLOYED")

        # Call restore_all_app_states to handle deployment
        self.restore_all_app_states()

    def restore_app_states(self):
        """Restore applications to their original states (DEPLOYED/RUNNING) and deployment groups.
        Only restores apps that had custom components."""
        logger.info("\n=== Restoring Application States ===")

        if not self.state.state.get('app_states'):
            logger.warning("[WARN] No application states found. Run --analyze first.")
            return

        if not self.state.state['apps_with_components']:
            logger.warning("[WARN] No components were restored. Run --restore first.")
            return

        app_states = self.state.state['app_states']
        deployment_plans = self.state.state.get('deployment_plans', {})
        apps_with_components = set(self.state.state['apps_with_components'].keys())

        # Classify apps by target state (only those with components)
        app_state_iter = ((name, app_states.get(name, 'UNKNOWN')) for name in apps_with_components)
        apps_to_deploy, apps_to_start = self._classify_app_states(app_state_iter)

        # Display preview
        self._log_deploy_preview("Applications to DEPLOY", apps_to_deploy, deployment_plans)
        self._log_deploy_preview("Applications to DEPLOY and START", apps_to_start, deployment_plans)

        if not apps_to_deploy and not apps_to_start:
            logger.info("\n[INFO] All applications are already in CREATED state, no action needed")
            return

        if self.dry_run:
            logger.info("\n[DRY-RUN] Would restore application states with deployment plans")
            return

        self._deploy_and_start_apps(apps_to_deploy, apps_to_start, deployment_plans)

        self.state.set_phase('states_restored')
        logger.info("\n[OK] Application states restored")

    def restore_all_app_states(self):
        """Restore ALL applications to their original states (DEPLOYED/RUNNING), not just those with components.

        This is useful when you want to restore the entire environment state after an upgrade,
        regardless of whether apps had custom components.
        """
        logger.info("\n=== Restoring ALL Application States ===")

        if not self.state.state.get('app_states'):
            logger.warning("[WARN] No application states found. Run --analyze first.")
            return

        app_states = self.state.state['app_states']
        deployment_plans = self.state.state.get('deployment_plans', {})

        # Classify ALL apps by target state
        apps_to_deploy, apps_to_start = self._classify_app_states(app_states.items())

        # Display preview
        self._log_deploy_preview("Applications to DEPLOY", apps_to_deploy, deployment_plans)
        self._log_deploy_preview("Applications to DEPLOY and START", apps_to_start, deployment_plans)

        if not apps_to_deploy and not apps_to_start:
            logger.info("\n[INFO] All applications are already in CREATED state, no action needed")
            return

        if self.dry_run:
            logger.info("\n[DRY-RUN] Would restore application states with deployment plans")
            return

        self._deploy_and_start_apps(apps_to_deploy, apps_to_start, deployment_plans)

        self.state.set_phase('all_states_restored')
        logger.info("\n[OK] All application states restored")

    def prepare_for_upgrade(self):
        """Run all pre-upgrade steps"""
        logger.info("\n" + "="*60)
        logger.info("PREPARE FOR UPGRADE - Running all pre-upgrade steps")
        logger.info("="*60)

        self.analyze()
        self.remove_from_apps()
        self.unload_components()

        logger.info("\n" + "="*60)
        logger.info("PRE-UPGRADE COMPLETE")
        logger.info("="*60)
        logger.info("\nNext steps:")
        logger.info("1. Upgrade Striim to the new version")
        logger.info("2. Upload new OP/UDF files via UI")
        logger.info("3. Run: python striim_upgrade_manager.py --complete-upgrade")

    def complete_upgrade(self):
        """Run all post-upgrade steps"""
        logger.info("\n" + "="*60)
        logger.info("COMPLETE UPGRADE - Running all post-upgrade steps")
        logger.info("="*60)

        logger.info("\n[INFO] Make sure you have uploaded the new component files!")
        logger.info("[INFO] Use --load-components --component-path <path> for each component")
        logger.info("\nThen run --restore-to-apps to add them back to applications")


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

  # After upgrade (restore components AND deploy/start apps automatically)
  python striim_upgrade_manager.py --load-components --component-path UploadedFiles/MyOP.scm
  python striim_upgrade_manager.py --restore-to-apps  # This now auto-deploys/starts apps!

  # Manual deployment control (if needed)
  python striim_upgrade_manager.py --restore-app-states          # Apps with OPs/UDFs only
  python striim_upgrade_manager.py --restore-all-app-states      # ALL apps

  # Dry run mode (preview any action)
  python striim_upgrade_manager.py --dry-run --remove-from-apps
  python striim_upgrade_manager.py --dry-run --restore-to-apps   # Preview component restore + deployment
  python striim_upgrade_manager.py --dry-run --restore-all-app-states

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
                       help='Restore OPs/UDFs to apps (ALTER, CREATE, RECOMPILE) and auto-deploy/start apps')
    parser.add_argument('--restore-app-states', action='store_true',
                       help='Restore applications to their original states (DEPLOYED/RUNNING) - only apps with OPs/UDFs')
    parser.add_argument('--restore-all-app-states', action='store_true',
                       help='Restore ALL applications to their original states (DEPLOYED/RUNNING) - regardless of components')
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

    # Initialize logging
    setup_logging()

    # Handle status and reset without authentication
    if args.status:
        state = UpgradeState()
        logger.info("\n=== Upgrade State ===")
        logger.info(json.dumps(state.state, indent=2))
        return

    if args.reset_state:
        if os.path.exists(STATE_FILE):
            backup_name = f"{STATE_FILE}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            os.rename(STATE_FILE, backup_name)
            logger.info(f"[OK] State backed up to {backup_name}")
        else:
            logger.info("[INFO] No state file to reset")
        return

    # Initialize API and state
    state = UpgradeState()
    api = StriimAPI()

    if not api.authenticate():
        logger.error("[ERROR] Authentication failed. Check config.py settings.")
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
        elif args.restore_all_app_states:
            manager.restore_all_app_states()
        elif args.prepare_for_upgrade:
            manager.prepare_for_upgrade()
        elif args.complete_upgrade:
            manager.complete_upgrade()
        else:
            parser.print_help()
            logger.info("\n[INFO] No action specified. Use one of the action flags above.")
    except KeyboardInterrupt:
        logger.warning("\n\n[WARN] Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"\n[ERROR] Unexpected error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()

