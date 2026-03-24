#!/usr/bin/env python3
"""
Striim Platform Upgrade Helper

Automates the Striim platform upgrade process including:
- Version detection and backup
- Metadata backup (Derby/PostgreSQL/MySQL)
- Platform upgrade (stop, uninstall, install, start)
- Rollback support
- Remote mode via SSH for cluster upgrades

Author: Striim Upgrade Automation
Version: 1.0.0
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Optional SSH support
try:
    import paramiko
    SSH_AVAILABLE = True
except ImportError:
    SSH_AVAILABLE = False
    logging.debug("paramiko not available - SSH remote mode disabled")


# ============================================================================
# Configuration and Constants
# ============================================================================

DEFAULT_STRIIM_HOME = "/opt/striim"
DEFAULT_BACKUP_BASE_DIR = "/opt/striim-backups"
PLATFORM_JAR_PATTERN = r"Platform-(\d+\.\d+\.\d+\.\d+)\.jar"
ELASTICSEARCH_DIR = "elasticsearch"

# Metadata types
METADATA_DERBY = "derby"
METADATA_POSTGRESQL = "postgresql"
METADATA_MYSQL = "mysql"


# ============================================================================
# Logging Setup
# ============================================================================

def setup_logging(log_file: Optional[str] = None, verbose: bool = False):
    """
    Setup logging configuration

    Args:
        log_file: Path to log file (optional)
        verbose: Enable debug logging
    """
    log_level = logging.DEBUG if verbose else logging.INFO
    log_format = '%(asctime)s [%(levelname)s] %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(log_format, date_format))

    handlers = [console_handler]

    # File handler (if specified)
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)  # Always debug in file
        file_handler.setFormatter(logging.Formatter(log_format, date_format))
        handlers.append(file_handler)

    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt=date_format,
        handlers=handlers
    )


# ============================================================================
# Version Detection
# ============================================================================

def get_striim_version(striim_home: str = DEFAULT_STRIIM_HOME) -> Dict[str, any]:
    """
    Detect Striim version from Platform-*.jar in lib/ directory

    Args:
        striim_home: Striim installation directory

    Returns:
        Dictionary with version information:
        {
            'version': '5.2.0.4',
            'major': 5,
            'minor': 2,
            'patch': 0,
            'build': 4,
            'jar_path': '/opt/striim/lib/Platform-5.2.0.4.jar'
        }

    Raises:
        FileNotFoundError: If lib directory or Platform JAR not found
        ValueError: If version cannot be parsed
    """
    lib_dir = os.path.join(striim_home, "lib")

    if not os.path.exists(lib_dir):
        raise FileNotFoundError(f"Striim lib directory not found: {lib_dir}")

    # Search for Platform-*.jar
    for filename in os.listdir(lib_dir):
        match = re.match(PLATFORM_JAR_PATTERN, filename)
        if match:
            version_str = match.group(1)
            parts = version_str.split('.')

            return {
                'version': version_str,
                'major': int(parts[0]),
                'minor': int(parts[1]),
                'patch': int(parts[2]),
                'build': int(parts[3]),
                'jar_path': os.path.join(lib_dir, filename)
            }

    raise ValueError(f"Platform JAR not found in {lib_dir}")


def generate_backup_name(version: str) -> str:
    """
    Generate version-based backup directory name

    Args:
        version: Version string (e.g., "5.2.0.4")

    Returns:
        Backup directory name (e.g., "Striim_5_2_0_4")
    """
    return f"Striim_{version.replace('.', '_')}"


# ============================================================================
# Pre-Flight Checks
# ============================================================================



def check_disk_space(path: str, required_gb: float) -> Tuple[bool, float]:
    """
    Check if sufficient disk space is available

    Args:
        path: Path to check
        required_gb: Required space in GB

    Returns:
        Tuple of (is_sufficient, available_gb)
    """
    try:
        stat = os.statvfs(path)
        available_bytes = stat.f_bavail * stat.f_frsize
        available_gb = available_bytes / (1024 ** 3)

        return (available_gb >= required_gb, available_gb)
    except Exception as e:
        logging.error(f"Failed to check disk space: {e}")
        return (False, 0.0)


def check_striim_running(striim_home: str = DEFAULT_STRIIM_HOME) -> bool:
    """
    Check if Striim service is running

    Returns:
        True if running, False otherwise
    """
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', 'striim-node'],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def detect_os_type() -> str:
    """
    Detect OS type (rhel or ubuntu)

    Returns:
        'rhel' or 'ubuntu'

    Raises:
        RuntimeError: If OS type cannot be determined
    """
    if os.path.exists('/etc/redhat-release'):
        return 'rhel'
    elif os.path.exists('/etc/lsb-release'):
        with open('/etc/lsb-release') as f:
            if 'Ubuntu' in f.read():
                return 'ubuntu'

    raise RuntimeError("Could not detect OS type (expected RHEL or Ubuntu)")


def run_preflight_checks(striim_home: str, skip_java: bool = False) -> bool:
    """
    Run all pre-flight checks

    Args:
        striim_home: Striim installation directory
        skip_java: Skip Java version check

    Returns:
        True if all checks pass, False otherwise
    """
    logging.info("=== Pre-Flight Checks ===\n")

    all_passed = True

    # Check root privileges
    if not check_root_privileges():
        logging.error("[FAIL] Must run with sudo/root privileges")
        all_passed = False
    else:
        logging.info("[OK] Running with sudo/root privileges")

    # Check Striim installation exists
    if not os.path.exists(striim_home):
        logging.error(f"[FAIL] Striim installation not found: {striim_home}")
        all_passed = False
    else:
        logging.info(f"[OK] Striim installation found: {striim_home}")

    # Check Striim version
    try:
        version_info = get_striim_version(striim_home)
        logging.info(f"[OK] Current Striim version: {version_info['version']}")
    except Exception as e:
        logging.error(f"[FAIL] Could not detect Striim version: {e}")
        all_passed = False

    # Check Java version
    if not skip_java:
        is_valid, java_version = check_java_version(required_major=17)
        if is_valid:
            logging.info(f"[OK] Java version: {java_version}")
        else:
            logging.error(f"[FAIL] Java 17 required for Striim 5.4.x")
            logging.error(f"      Current: {java_version}")
            logging.error(f"      Install from: https://www.oracle.com/java/technologies/downloads/#java17")
            all_passed = False

    # Check disk space (need 2x Striim size)
    try:
        striim_size_gb = get_directory_size(striim_home) / (1024 ** 3)
        required_gb = striim_size_gb * 2
        is_sufficient, available_gb = check_disk_space(striim_home, required_gb)

        if is_sufficient:
            logging.info(f"[OK] Disk space: {available_gb:.2f} GB available (need {required_gb:.2f} GB)")
        else:
            logging.error(f"[FAIL] Insufficient disk space: {available_gb:.2f} GB available (need {required_gb:.2f} GB)")
            all_passed = False
    except Exception as e:
        logging.warning(f"[WARN] Could not check disk space: {e}")

    # Check Striim is running
    if check_striim_running():
        logging.info("[OK] Striim service is running")
    else:
        logging.warning("[WARN] Striim service is not running")

    # Detect OS type
    try:
        os_type = detect_os_type()
        logging.info(f"[OK] OS type: {os_type.upper()}")
    except Exception as e:
        logging.error(f"[FAIL] {e}")
        all_passed = False

    logging.info("")
    return all_passed


def get_directory_size(path: str) -> int:
    """
    Get total size of directory in bytes

    Args:
        path: Directory path

    Returns:
        Size in bytes
    """
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            if os.path.exists(filepath):
                total_size += os.path.getsize(filepath)
    return total_size


# ============================================================================
# Metadata Detection and Backup
# ============================================================================

def detect_metadata_type(striim_home: str) -> str:
    """
    Detect metadata repository type from startUp.properties

    Args:
        striim_home: Striim installation directory

    Returns:
        Metadata type: 'derby', 'postgresql', or 'mysql'
    """
    startup_props = os.path.join(striim_home, "conf", "startUp.properties")

    if not os.path.exists(startup_props):
        logging.warning(f"startUp.properties not found, assuming Derby")
        return METADATA_DERBY

    try:
        with open(startup_props, 'r') as f:
            content = f.read()

            if 'postgresql' in content.lower():
                return METADATA_POSTGRESQL
            elif 'mysql' in content.lower():
                return METADATA_MYSQL
            else:
                return METADATA_DERBY
    except Exception as e:
        logging.warning(f"Could not read startUp.properties: {e}, assuming Derby")
        return METADATA_DERBY


def backup_metadata(striim_home: str, backup_dir: str) -> bool:
    """
    Backup metadata repository based on type

    Args:
        striim_home: Striim installation directory
        backup_dir: Backup destination directory

    Returns:
        True if successful, False otherwise
    """
    metadata_type = detect_metadata_type(striim_home)
    metadata_backup_dir = os.path.join(backup_dir, "metadata-backup")
    os.makedirs(metadata_backup_dir, exist_ok=True)

    logging.info(f"Backing up {metadata_type.upper()} metadata...")

    try:
        if metadata_type == METADATA_DERBY:
            # Copy Derby directory
            derby_src = os.path.join(striim_home, "derby")
            if os.path.exists(derby_src):
                derby_dst = os.path.join(metadata_backup_dir, "derby")
                shutil.copytree(derby_src, derby_dst, dirs_exist_ok=True)
                logging.info(f"  [OK] Derby database backed up to {derby_dst}")
                return True
            else:
                logging.warning(f"  [WARN] Derby directory not found: {derby_src}")
                return False

        elif metadata_type == METADATA_POSTGRESQL:
            # TODO: Implement PostgreSQL backup with pg_dump
            logging.warning(f"  [WARN] PostgreSQL backup not yet implemented")
            logging.warning(f"  [WARN] Please backup PostgreSQL manually")
            return True

        elif metadata_type == METADATA_MYSQL:
            # TODO: Implement MySQL backup with mysqldump
            logging.warning(f"  [WARN] MySQL backup not yet implemented")
            logging.warning(f"  [WARN] Please backup MySQL manually")
            return True

    except Exception as e:
        logging.error(f"  [FAIL] Metadata backup failed: {e}")
        return False

    return False



# ============================================================================
# Backup Functions
# ============================================================================

def copy_with_exclusions(src: str, dst: str, exclude: List[str] = None) -> bool:
    """
    Copy directory tree with exclusions

    Args:
        src: Source directory
        dst: Destination directory
        exclude: List of directory names to exclude

    Returns:
        True if successful, False otherwise
    """
    if exclude is None:
        exclude = []

    def should_exclude(path: str) -> bool:
        """Check if path should be excluded"""
        for excl in exclude:
            if excl in path:
                return True
        return False

    try:
        os.makedirs(dst, exist_ok=True)

        for item in os.listdir(src):
            src_path = os.path.join(src, item)
            dst_path = os.path.join(dst, item)

            # Skip excluded directories
            if should_exclude(src_path):
                logging.debug(f"  Excluding: {item}")
                continue

            if os.path.isdir(src_path):
                shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
            else:
                shutil.copy2(src_path, dst_path)

        return True

    except Exception as e:
        logging.error(f"Copy failed: {e}")
        return False


def backup_config_files(striim_home: str, backup_dir: str) -> bool:
    """
    Backup critical configuration files

    Args:
        striim_home: Striim installation directory
        backup_dir: Backup destination directory

    Returns:
        True if successful, False otherwise
    """
    conf_src = os.path.join(striim_home, "conf")
    conf_dst = os.path.join(backup_dir, "conf")

    os.makedirs(conf_dst, exist_ok=True)

    # Critical files to backup
    critical_files = [
        "startUp.properties",
        "sks.jks",
        "sksKey.pwd"
    ]

    # Also backup all sks* files
    try:
        for filename in os.listdir(conf_src):
            if filename in critical_files or filename.startswith("sks"):
                src_file = os.path.join(conf_src, filename)
                dst_file = os.path.join(conf_dst, filename)

                if os.path.isfile(src_file):
                    shutil.copy2(src_file, dst_file)
                    logging.debug(f"  Backed up: {filename}")

        logging.info(f"  [OK] Configuration files backed up to {conf_dst}")
        return True

    except Exception as e:
        logging.error(f"  [FAIL] Config backup failed: {e}")
        return False


def create_backup_manifest(version_backup: str, timestamped_backup: str,
                          version: str, timestamp: str) -> bool:
    """
    Create backup manifest JSON file

    Args:
        version_backup: Path to version-based backup
        timestamped_backup: Path to timestamped backup
        version: Striim version
        timestamp: Backup timestamp

    Returns:
        True if successful, False otherwise
    """
    manifest = {
        "backup_timestamp": timestamp,
        "striim_version": version,
        "version_backup_dir": version_backup,
        "timestamped_backup_dir": timestamped_backup,
        "metadata_type": detect_metadata_type(version_backup),
        "created_by": "striim_upgrade_helper.py"
    }

    manifest_file = os.path.join(timestamped_backup, "backup-manifest.json")

    try:
        with open(manifest_file, 'w') as f:
            json.dump(manifest, f, indent=2)

        logging.debug(f"  Manifest created: {manifest_file}")
        return True

    except Exception as e:
        logging.error(f"  [FAIL] Manifest creation failed: {e}")
        return False


def backup_striim(striim_home: str, backup_base_dir: str, version: str) -> Dict[str, str]:
    """
    Complete backup of Striim installation

    Creates:
    1. /opt/Striim_5_2_0_4/ - Full copy of installation
    2. /opt/striim-backups/backup-<timestamp>/ - Timestamped backup

    Args:
        striim_home: Striim installation directory
        backup_base_dir: Base directory for backups
        version: Striim version string

    Returns:
        Dictionary with backup paths:
        {
            'version_backup': '/opt/Striim_5_2_0_4',
            'timestamped_backup': '/opt/striim-backups/backup-20260324-143000',
            'timestamp': '20260324-143000'
        }

    Raises:
        RuntimeError: If backup fails
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    # Version-based backup directory
    version_backup_name = generate_backup_name(version)
    version_backup_dir = os.path.join(os.path.dirname(striim_home), version_backup_name)

    # Timestamped backup directory
    timestamped_backup_dir = os.path.join(backup_base_dir, f"backup-{timestamp}")

    logging.info("\n=== Backing Up Striim ===\n")
    logging.info(f"Source: {striim_home}")
    logging.info(f"Version backup: {version_backup_dir}")
    logging.info(f"Timestamped backup: {timestamped_backup_dir}\n")

    # 1. Full copy to version-based directory (exclude elasticsearch)
    logging.info("[1/4] Copying full installation...")
    if not copy_with_exclusions(striim_home, version_backup_dir, exclude=[ELASTICSEARCH_DIR]):
        raise RuntimeError("Failed to create version backup")
    logging.info(f"  [OK] Full installation copied to {version_backup_dir}\n")

    # 2. Backup metadata
    logging.info("[2/4] Backing up metadata repository...")
    if not backup_metadata(striim_home, timestamped_backup_dir):
        logging.warning("  [WARN] Metadata backup incomplete\n")
    else:
        logging.info("")

    # 3. Backup critical config files
    logging.info("[3/4] Backing up configuration files...")
    if not backup_config_files(striim_home, timestamped_backup_dir):
        raise RuntimeError("Failed to backup configuration files")
    logging.info("")

    # 4. Create backup manifest
    logging.info("[4/4] Creating backup manifest...")
    if not create_backup_manifest(version_backup_dir, timestamped_backup_dir, version, timestamp):
        logging.warning("  [WARN] Manifest creation failed\n")
    else:
        logging.info(f"  [OK] Manifest created\n")

    logging.info("[OK] Backup complete\n")

    return {
        'version_backup': version_backup_dir,
        'timestamped_backup': timestamped_backup_dir,
        'timestamp': timestamp
    }



# ============================================================================
# SSH Remote Mode Support (Phase 3)
# ============================================================================

class SSHNodeManager:
    """Manage SSH connections and remote command execution for cluster upgrades"""

    def __init__(self, host: str, username: str, password: Optional[str] = None,
                 key_file: Optional[str] = None, port: int = 22):
        """
        Initialize SSH connection manager

        Args:
            host: Remote host address
            username: SSH username
            password: SSH password (optional if using key)
            key_file: Path to SSH private key (optional if using password)
            port: SSH port (default: 22)
        """
        if not SSH_AVAILABLE:
            raise RuntimeError("paramiko not installed. Install with: pip install paramiko")

        self.host = host
        self.username = username
        self.password = password
        self.key_file = key_file
        self.port = port
        self.client = None
        self.connected = False

    def connect(self) -> bool:
        """
        Establish SSH connection

        Returns:
            True if successful, False otherwise
        """
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs = {
                'hostname': self.host,
                'port': self.port,
                'username': self.username,
                'timeout': 30
            }

            if self.key_file:
                connect_kwargs['key_filename'] = self.key_file
            elif self.password:
                connect_kwargs['password'] = self.password
            else:
                raise ValueError("Either password or key_file must be provided")

            self.client.connect(**connect_kwargs)
            self.connected = True
            logging.info(f"  [OK] Connected to {self.host}")
            return True

        except Exception as e:
            logging.error(f"  [FAIL] Failed to connect to {self.host}: {e}")
            self.connected = False
            return False

    def disconnect(self):
        """Close SSH connection"""
        if self.client:
            self.client.close()
            self.connected = False
            logging.debug(f"Disconnected from {self.host}")

    def execute_command(self, command: str, timeout: int = 300) -> Tuple[int, str, str]:
        """
        Execute command on remote host

        Args:
            command: Command to execute
            timeout: Command timeout in seconds

        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        if not self.connected:
            raise RuntimeError(f"Not connected to {self.host}")

        try:
            stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
            exit_code = stdout.channel.recv_exit_status()
            stdout_str = stdout.read().decode('utf-8')
            stderr_str = stderr.read().decode('utf-8')

            return exit_code, stdout_str, stderr_str

        except Exception as e:
            logging.error(f"Command execution failed on {self.host}: {e}")
            return -1, "", str(e)

    def execute_sudo_command(self, command: str, timeout: int = 300) -> Tuple[int, str, str]:
        """
        Execute command with sudo on remote host

        Args:
            command: Command to execute
            timeout: Command timeout in seconds

        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        sudo_cmd = f"sudo -S {command}"
        return self.execute_command(sudo_cmd, timeout)

    def file_exists(self, path: str) -> bool:
        """
        Check if file exists on remote host

        Args:
            path: File path to check

        Returns:
            True if file exists, False otherwise
        """
        exit_code, _, _ = self.execute_command(f"test -f {path} && echo 'exists'")
        return exit_code == 0

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        """
        Upload file to remote host via SCP

        Args:
            local_path: Local file path
            remote_path: Remote file path

        Returns:
            True if successful, False otherwise
        """
        if not self.connected:
            raise RuntimeError(f"Not connected to {self.host}")

        try:
            sftp = self.client.open_sftp()
            sftp.put(local_path, remote_path)
            sftp.close()
            logging.info(f"  [OK] Uploaded {local_path} to {self.host}:{remote_path}")
            return True

        except Exception as e:
            logging.error(f"  [FAIL] Failed to upload file to {self.host}: {e}")
            return False

    def download_file(self, remote_path: str, local_path: str) -> bool:
        """
        Download file from remote host via SCP

        Args:
            remote_path: Remote file path
            local_path: Local file path

        Returns:
            True if successful, False otherwise
        """
        if not self.connected:
            raise RuntimeError(f"Not connected to {self.host}")

        try:
            sftp = self.client.open_sftp()
            sftp.get(remote_path, local_path)
            sftp.close()
            logging.info(f"  [OK] Downloaded {self.host}:{remote_path} to {local_path}")
            return True

        except Exception as e:
            logging.error(f"  [FAIL] Failed to download file from {self.host}: {e}")
            return False

    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.disconnect()


# ============================================================================
# Upgrade Manager Integration
# ============================================================================

def check_upgrade_manager_integration(auto_mode: bool = False) -> bool:
    """
    Check if striim_upgrade_manager.py exists and offer integration

    Args:
        auto_mode: If True, skip prompts

    Returns:
        True if component preparation was run, False otherwise
    """
    if not os.path.exists('striim_upgrade_manager.py'):
        logging.info("\n[INFO] striim_upgrade_manager.py not found in current directory")
        logging.info("[INFO] You may need to manually handle OPs/UDFs before upgrade\n")
        return False

    logging.info("\n=== Component Management Integration ===\n")
    logging.info("[INFO] Found striim_upgrade_manager.py")
    logging.info("[INFO] This script can handle OP/UDF removal before upgrade\n")

    if auto_mode:
        logging.info("[INFO] Auto mode: Skipping component preparation")
        logging.info("[INFO] Run manually if needed: python3 striim_upgrade_manager.py --prepare-for-upgrade\n")
        return False

    response = input("Run component preparation now? (yes/no): ").strip().lower()

    if response in ['yes', 'y']:
        logging.info("\n[INFO] Running component preparation...\n")
        try:
            result = subprocess.run(
                ['python3', 'striim_upgrade_manager.py', '--prepare-for-upgrade'],
                capture_output=False,
                text=True
            )

            if result.returncode != 0:
                logging.error("\n[ERROR] Component preparation failed")
                logging.error("[ERROR] Please fix issues and try again\n")
                return False

            logging.info("\n[OK] Component preparation complete\n")
            return True

        except Exception as e:
            logging.error(f"\n[ERROR] Failed to run component preparation: {e}\n")
            return False
    else:
        logging.info("\n[INFO] Skipping component preparation")
        logging.info("[INFO] You can run it manually later if needed\n")
        return False


# ============================================================================
# Striim Service Management
# ============================================================================

def stop_striim_service() -> bool:
    """
    Stop Striim service

    Returns:
        True if successful, False otherwise
    """
    logging.info("Stopping Striim service...")
    try:
        result = subprocess.run(
            ['systemctl', 'stop', 'striim-node'],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode == 0:
            logging.info("  [OK] Striim service stopped\n")
            return True
        else:
            logging.error(f"  [FAIL] Failed to stop Striim: {result.stderr}\n")
            return False

    except subprocess.TimeoutExpired:
        logging.error("  [FAIL] Timeout stopping Striim service\n")
        return False
    except Exception as e:
        logging.error(f"  [FAIL] Error stopping Striim: {e}\n")
        return False


def start_striim_service() -> bool:
    """
    Start Striim service

    Returns:
        True if successful, False otherwise
    """
    logging.info("Starting Striim service...")
    try:
        result = subprocess.run(
            ['systemctl', 'start', 'striim-node'],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode == 0:
            logging.info("  [OK] Striim service started\n")
            return True
        else:
            logging.error(f"  [FAIL] Failed to start Striim: {result.stderr}\n")
            return False

    except subprocess.TimeoutExpired:
        logging.error("  [FAIL] Timeout starting Striim service\n")
        return False
    except Exception as e:
        logging.error(f"  [FAIL] Error starting Striim: {e}\n")
        return False


def wait_for_striim_startup(timeout: int = 300) -> bool:
    """
    Wait for Striim to fully start up

    Args:
        timeout: Maximum time to wait in seconds

    Returns:
        True if Striim started successfully, False otherwise
    """
    logging.info(f"Waiting for Striim to start (timeout: {timeout}s)...")

    start_time = time.time()
    while time.time() - start_time < timeout:
        if check_striim_running():
            elapsed = int(time.time() - start_time)
            logging.info(f"  [OK] Striim is running (took {elapsed}s)\n")
            return True

        time.sleep(5)

    logging.error(f"  [FAIL] Striim did not start within {timeout}s\n")
    return False


# ============================================================================
# Main Upgrade Workflow
# ============================================================================

def check_version_command(striim_home: str):
    """Check and display current Striim version"""
    try:
        version_info = get_striim_version(striim_home)
        print(f"\nCurrent Striim Version: {version_info['version']}")
        print(f"  Major: {version_info['major']}")
        print(f"  Minor: {version_info['minor']}")
        print(f"  Patch: {version_info['patch']}")
        print(f"  Build: {version_info['build']}")
        print(f"  JAR: {version_info['jar_path']}\n")
    except Exception as e:
        logging.error(f"Failed to detect version: {e}")
        sys.exit(1)



# ============================================================================
# Package Management
# ============================================================================

def uninstall_striim(os_type: str) -> bool:
    """
    Uninstall current Striim version

    Args:
        os_type: 'rhel' or 'ubuntu'

    Returns:
        True if successful, False otherwise
    """
    logging.info(f"Uninstalling Striim ({os_type.upper()})...")

    try:
        if os_type == 'rhel':
            cmd = ['rpm', '-e', 'striim-node']
        else:  # ubuntu
            cmd = ['dpkg', '--remove', 'striim-node']

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode == 0:
            logging.info("  [OK] Striim uninstalled\n")
            return True
        else:
            logging.error(f"  [FAIL] Uninstall failed: {result.stderr}\n")
            return False

    except subprocess.TimeoutExpired:
        logging.error("  [FAIL] Timeout during uninstall\n")
        return False
    except Exception as e:
        logging.error(f"  [FAIL] Error during uninstall: {e}\n")
        return False


def install_striim(package_path: str, os_type: str) -> bool:
    """
    Install new Striim version

    Args:
        package_path: Path to Striim package
        os_type: 'rhel' or 'ubuntu'

    Returns:
        True if successful, False otherwise
    """
    logging.info(f"Installing Striim from {package_path}...")

    if not os.path.exists(package_path):
        logging.error(f"  [FAIL] Package not found: {package_path}\n")
        return False

    try:
        if os_type == 'rhel':
            cmd = ['rpm', '-ivh', package_path]
        else:  # ubuntu
            cmd = ['dpkg', '-i', package_path]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode == 0:
            logging.info("  [OK] Striim installed\n")
            return True
        else:
            logging.error(f"  [FAIL] Install failed: {result.stderr}\n")
            return False

    except subprocess.TimeoutExpired:
        logging.error("  [FAIL] Timeout during install\n")
        return False
    except Exception as e:
        logging.error(f"  [FAIL] Error during install: {e}\n")
        return False


def run_metadata_upgrade(striim_home: str) -> bool:
    """
    Run Striim metadata upgrade script

    Args:
        striim_home: Striim installation directory

    Returns:
        True if successful, False otherwise
    """
    upgrade_script = os.path.join(striim_home, "bin", "upgrade.sh")

    if not os.path.exists(upgrade_script):
        logging.error(f"  [FAIL] Upgrade script not found: {upgrade_script}\n")
        return False

    logging.info("Running metadata upgrade...")

    try:
        # Make script executable
        os.chmod(upgrade_script, 0o755)

        result = subprocess.run(
            [upgrade_script, '-a', 'forward'],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=striim_home
        )

        if result.returncode == 0:
            logging.info("  [OK] Metadata upgrade complete\n")
            return True
        else:
            logging.error(f"  [FAIL] Metadata upgrade failed: {result.stderr}\n")
            return False

    except subprocess.TimeoutExpired:
        logging.error("  [FAIL] Timeout during metadata upgrade\n")
        return False
    except Exception as e:
        logging.error(f"  [FAIL] Error during metadata upgrade: {e}\n")
        return False


# ============================================================================
# Rollback Support
# ============================================================================

def rollback_striim(backup_dir: str, striim_home: str, os_type: str) -> bool:
    """
    Rollback to previous Striim version from backup

    Args:
        backup_dir: Path to version backup directory (e.g., /opt/Striim_5_2_0_4)
        striim_home: Striim installation directory
        os_type: 'rhel' or 'ubuntu'

    Returns:
        True if successful, False otherwise
    """
    logging.info("\n=== Rolling Back to Previous Version ===\n")
    logging.info(f"Backup directory: {backup_dir}")
    logging.info(f"Target directory: {striim_home}\n")

    if not os.path.exists(backup_dir):
        logging.error(f"[FAIL] Backup directory not found: {backup_dir}\n")
        return False

    try:
        # 1. Stop current Striim
        logging.info("[1/5] Stopping Striim service...")
        if not stop_striim_service():
            logging.warning("  [WARN] Could not stop Striim (may not be running)\n")

        # 2. Uninstall current version
        logging.info("[2/5] Uninstalling current version...")
        if not uninstall_striim(os_type):
            logging.warning("  [WARN] Uninstall failed, continuing anyway\n")

        # 3. Remove current installation directory
        logging.info("[3/5] Removing current installation...")
        if os.path.exists(striim_home):
            shutil.rmtree(striim_home)
            logging.info(f"  [OK] Removed {striim_home}\n")

        # 4. Restore from backup
        logging.info("[4/5] Restoring from backup...")
        shutil.copytree(backup_dir, striim_home, dirs_exist_ok=True)
        logging.info(f"  [OK] Restored from {backup_dir}\n")

        # 5. Start Striim
        logging.info("[5/5] Starting Striim service...")
        if not start_striim_service():
            logging.error("  [FAIL] Could not start Striim\n")
            return False

        # Wait for startup
        if not wait_for_striim_startup():
            logging.error("  [FAIL] Striim did not start properly\n")
            return False

        logging.info("[OK] Rollback complete\n")
        return True

    except Exception as e:
        logging.error(f"[FAIL] Rollback failed: {e}\n")
        return False


def rollback_command(backup_dir: str, striim_home: str):
    """Execute rollback command"""
    try:
        os_type = detect_os_type()
    except Exception as e:
        logging.error(f"Failed to detect OS type: {e}")
        sys.exit(1)

    if rollback_striim(backup_dir, striim_home, os_type):
        logging.info("Rollback successful!")
        sys.exit(0)
    else:
        logging.error("Rollback failed!")
        sys.exit(1)




# ============================================================================
# Complete Upgrade Workflow
# ============================================================================

def perform_upgrade(striim_home: str, package_path: str, backup_info: Dict[str, str],
                   os_type: str, auto_rollback: bool = True) -> bool:
    """
    Perform complete Striim upgrade

    Args:
        striim_home: Striim installation directory
        package_path: Path to new Striim package
        backup_info: Backup information from backup_striim()
        os_type: 'rhel' or 'ubuntu'
        auto_rollback: Automatically rollback on failure

    Returns:
        True if successful, False otherwise
    """
    logging.info("\n=== Upgrading Striim ===\n")

    # Save startUp.properties location
    startup_props_src = os.path.join(striim_home, "conf", "startUp.properties")
    startup_props_backup = os.path.join(backup_info['timestamped_backup'], "conf", "startUp.properties")

    try:
        # 1. Stop Striim
        logging.info("[1/6] Stopping Striim service...")
        if not stop_striim_service():
            raise RuntimeError("Failed to stop Striim service")

        # 2. Move startUp.properties to safe location (already in backup, but be safe)
        logging.info("[2/6] Securing configuration files...")
        temp_startup = "/tmp/startUp.properties.upgrade"
        if os.path.exists(startup_props_src):
            shutil.copy2(startup_props_src, temp_startup)
            logging.info(f"  [OK] Configuration secured\n")
        else:
            logging.warning(f"  [WARN] startUp.properties not found\n")

        # 3. Uninstall old version
        logging.info("[3/6] Uninstalling old version...")
        if not uninstall_striim(os_type):
            raise RuntimeError("Failed to uninstall old version")

        # 4. Install new version
        logging.info("[4/6] Installing new version...")
        if not install_striim(package_path, os_type):
            raise RuntimeError("Failed to install new version")

        # 5. Restore startUp.properties
        logging.info("[5/6] Restoring configuration...")
        if os.path.exists(temp_startup):
            shutil.copy2(temp_startup, startup_props_src)
            os.remove(temp_startup)
            logging.info(f"  [OK] Configuration restored\n")
        elif os.path.exists(startup_props_backup):
            shutil.copy2(startup_props_backup, startup_props_src)
            logging.info(f"  [OK] Configuration restored from backup\n")
        else:
            logging.warning(f"  [WARN] Could not restore startUp.properties\n")

        # 6. Run metadata upgrade
        logging.info("[6/6] Running metadata upgrade...")
        if not run_metadata_upgrade(striim_home):
            raise RuntimeError("Metadata upgrade failed")

        logging.info("[OK] Upgrade steps complete\n")
        return True

    except Exception as e:
        logging.error(f"\n[FAIL] Upgrade failed: {e}\n")

        if auto_rollback:
            logging.info("Attempting automatic rollback...")
            if rollback_striim(backup_info['version_backup'], striim_home, os_type):
                logging.info("[OK] Rollback successful")
            else:
                logging.error("[FAIL] Rollback failed - manual intervention required")

        return False


def prompt_user_preparation(auto_mode: bool = False) -> bool:
    """
    Prompt user to prepare for upgrade

    Args:
        auto_mode: If True, skip prompts

    Returns:
        True if user confirms, False otherwise
    """
    if auto_mode:
        logging.info("[INFO] Auto mode: Skipping preparation prompts\n")
        return True

    logging.info("\n=== Application Preparation ===\n")
    logging.info("Before proceeding with the upgrade, you must:\n")
    logging.info("1. Stop all Forwarding Agents")
    logging.info("   - SSH to each Forwarding Agent host")
    logging.info("   - Run: sudo systemctl stop striim-agent\n")
    logging.info("2. Quiesce and undeploy applications:")
    logging.info("   - MSJet applications with CDDL Capture enabled")
    logging.info("   - Applications with persisted streams")
    logging.info("   - All other running/deployed applications\n")
    logging.info("3. (Optional) Use striim_upgrade_manager.py to handle OPs/UDFs:")
    logging.info("   python3 striim_upgrade_manager.py --prepare-for-upgrade\n")

    response = input("Have you completed these steps? (yes/no): ").strip().lower()

    if response in ['yes', 'y']:
        logging.info("\n[OK] Proceeding with upgrade\n")
        return True
    else:
        logging.info("\n[INFO] Please complete preparation steps and try again\n")
        return False


def verify_upgrade(striim_home: str, expected_version: Optional[str] = None) -> bool:
    """
    Verify upgrade was successful

    Args:
        striim_home: Striim installation directory
        expected_version: Expected version (optional)

    Returns:
        True if verification passed, False otherwise
    """
    logging.info("\n=== Verifying Upgrade ===\n")

    all_passed = True

    # 1. Check Striim is running
    logging.info("[1/3] Checking Striim service status...")
    if check_striim_running():
        logging.info("  [OK] Striim service is running\n")
    else:
        logging.error("  [FAIL] Striim service is not running\n")
        all_passed = False

    # 2. Check version
    logging.info("[2/3] Checking Striim version...")
    try:
        version_info = get_striim_version(striim_home)
        new_version = version_info['version']
        logging.info(f"  [OK] New version: {new_version}\n")

        if expected_version and new_version != expected_version:
            logging.warning(f"  [WARN] Expected {expected_version}, got {new_version}\n")
    except Exception as e:
        logging.error(f"  [FAIL] Could not detect version: {e}\n")
        all_passed = False

    # 3. Check logs for errors
    logging.info("[3/3] Checking Striim logs...")
    log_file = os.path.join(striim_home, "logs", "striim.log")
    if os.path.exists(log_file):
        try:
            # Check last 100 lines for errors
            with open(log_file, 'r') as f:
                lines = f.readlines()
                recent_lines = lines[-100:] if len(lines) > 100 else lines
                error_count = sum(1 for line in recent_lines if 'ERROR' in line or 'FATAL' in line)

                if error_count > 0:
                    logging.warning(f"  [WARN] Found {error_count} error(s) in recent logs\n")
                else:
                    logging.info(f"  [OK] No errors in recent logs\n")
        except Exception as e:
            logging.warning(f"  [WARN] Could not check logs: {e}\n")
    else:
        logging.warning(f"  [WARN] Log file not found: {log_file}\n")




# ============================================================================
# Remote Upgrade Functions (Phase 3)
# ============================================================================

def download_package_to_node(node: SSHNodeManager, package_url: str,
                             remote_path: str = "/tmp") -> Optional[str]:
    """
    Download Striim package directly to remote node

    Args:
        node: SSH node manager
        package_url: URL to download package from
        remote_path: Remote directory to download to

    Returns:
        Full path to downloaded package on remote node, or None if failed
    """
    # Extract filename from URL
    filename = package_url.split('/')[-1]
    remote_file = f"{remote_path}/{filename}"

    logging.info(f"Downloading package to {node.host}...")
    logging.info(f"  URL: {package_url}")
    logging.info(f"  Destination: {remote_file}")

    # Use wget or curl to download on remote node
    download_cmd = f"wget -O {remote_file} {package_url} || curl -o {remote_file} {package_url}"

    exit_code, stdout, stderr = node.execute_command(download_cmd, timeout=600)

    if exit_code == 0:
        logging.info(f"  [OK] Package downloaded to {node.host}:{remote_file}")
        return remote_file
    else:
        logging.error(f"  [FAIL] Download failed: {stderr}")
        return None


def remote_upgrade_node(node: SSHNodeManager, package_path: str,
                       striim_home: str = DEFAULT_STRIIM_HOME,
                       backup_dir: str = DEFAULT_BACKUP_BASE_DIR,
                       skip_java: bool = False,
                       auto_rollback: bool = True) -> bool:
    """
    Perform upgrade on a remote node via SSH

    Args:
        node: SSH node manager
        package_path: Path to package on remote node
        striim_home: Striim installation directory
        backup_dir: Backup directory
        skip_java: Skip Java 17 check
        auto_rollback: Enable automatic rollback on failure

    Returns:
        True if successful, False otherwise
    """
    logging.info(f"\n{'='*70}")
    logging.info(f"Upgrading Remote Node: {node.host}")
    logging.info(f"{'='*70}\n")

    try:
        # 1. Run pre-flight checks
        logging.info("[1/7] Running pre-flight checks...")

        # Check if Striim exists
        exit_code, _, _ = node.execute_command(f"test -d {striim_home}")
        if exit_code != 0:
            logging.error(f"  [FAIL] Striim not found at {striim_home}")
            return False

        # Check if package exists
        exit_code, _, _ = node.execute_command(f"test -f {package_path}")
        if exit_code != 0:
            logging.error(f"  [FAIL] Package not found: {package_path}")
            return False

        # Get current version
        exit_code, stdout, _ = node.execute_command(
            f"ls {striim_home}/lib/Platform-*.jar | head -1"
        )
        if exit_code != 0:
            logging.error(f"  [FAIL] Could not detect Striim version")
            return False

        jar_path = stdout.strip()
        version_match = re.search(r'Platform-(\d+\.\d+\.\d+\.\d+)\.jar', jar_path)
        if not version_match:
            logging.error(f"  [FAIL] Could not parse version from {jar_path}")
            return False

        current_version = version_match.group(1)
        logging.info(f"  [OK] Current version: {current_version}\n")

        # 2. Create backup
        logging.info("[2/7] Creating backup...")
        version_backup_name = generate_backup_name(current_version)
        version_backup_dir = f"/opt/{version_backup_name}"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        timestamped_backup_dir = f"{backup_dir}/backup-{timestamp}"

        # Create version backup (exclude elasticsearch)
        backup_cmd = f"sudo cp -r {striim_home} {version_backup_dir} && sudo rm -rf {version_backup_dir}/elasticsearch"
        exit_code, _, stderr = node.execute_sudo_command(backup_cmd, timeout=600)

        if exit_code != 0:
            logging.error(f"  [FAIL] Backup failed: {stderr}")
            return False

        logging.info(f"  [OK] Backup created: {version_backup_dir}\n")

        # 3. Stop Striim
        logging.info("[3/7] Stopping Striim service...")
        exit_code, _, _ = node.execute_sudo_command("systemctl stop striim-node", timeout=60)
        if exit_code != 0:
            logging.warning(f"  [WARN] Could not stop Striim service\n")
        else:
            logging.info(f"  [OK] Striim stopped\n")

        # 4. Detect OS and uninstall
        logging.info("[4/7] Uninstalling old version...")
        exit_code, stdout, _ = node.execute_command("cat /etc/os-release")
        os_type = 'rhel' if 'rhel' in stdout.lower() or 'centos' in stdout.lower() or 'red hat' in stdout.lower() else 'ubuntu'

        if os_type == 'rhel':
            uninstall_cmd = "rpm -e striim-node"
        else:
            uninstall_cmd = "dpkg --remove striim-node"

        exit_code, _, stderr = node.execute_sudo_command(uninstall_cmd, timeout=120)
        if exit_code != 0:
            logging.error(f"  [FAIL] Uninstall failed: {stderr}")
            return False

        logging.info(f"  [OK] Old version uninstalled\n")

        # 5. Install new version
        logging.info("[5/7] Installing new version...")
        if os_type == 'rhel':
            install_cmd = f"rpm -ivh {package_path}"
        else:
            install_cmd = f"dpkg -i {package_path}"

        exit_code, _, stderr = node.execute_sudo_command(install_cmd, timeout=300)
        if exit_code != 0:
            logging.error(f"  [FAIL] Install failed: {stderr}")
            if auto_rollback:
                logging.info("Attempting rollback...")
                # Restore from backup
                node.execute_sudo_command(f"rm -rf {striim_home}")
                node.execute_sudo_command(f"cp -r {version_backup_dir} {striim_home}")
                node.execute_sudo_command("systemctl start striim-node")
            return False

        logging.info(f"  [OK] New version installed\n")

        # 6. Run metadata upgrade
        logging.info("[6/7] Running metadata upgrade...")
        upgrade_cmd = f"cd {striim_home} && bin/upgrade.sh -a forward"
        exit_code, stdout, stderr = node.execute_sudo_command(upgrade_cmd, timeout=300)

        if exit_code != 0:
            logging.error(f"  [FAIL] Metadata upgrade failed: {stderr}")
            return False

        logging.info(f"  [OK] Metadata upgrade complete\n")

        # 7. Start Striim
        logging.info("[7/7] Starting Striim service...")
        exit_code, _, _ = node.execute_sudo_command("systemctl start striim-node", timeout=60)
        if exit_code != 0:
            logging.error(f"  [FAIL] Could not start Striim")
            return False

        logging.info(f"  [OK] Striim started\n")

        # Wait for startup
        logging.info("Waiting for Striim to start...")
        for i in range(60):  # Wait up to 5 minutes
            time.sleep(5)
            exit_code, _, _ = node.execute_command("systemctl is-active striim-node")
            if exit_code == 0:
                logging.info(f"  [OK] Striim is running\n")
                break
        else:
            logging.warning(f"  [WARN] Striim may not have started properly\n")

        logging.info(f"[OK] Remote upgrade complete for {node.host}\n")
        return True

    except Exception as e:
        logging.error(f"[FAIL] Remote upgrade failed: {e}")
        return False


def upgrade_cluster(nodes: List[Dict[str, str]], package_path: str = None,
                   package_url: str = None, parallel_download: bool = False,
                   **kwargs) -> Dict[str, bool]:
    """
    Upgrade multiple nodes in a cluster

    Args:
        nodes: List of node configs [{'host': 'x', 'username': 'y', 'password': 'z'}, ...]
        package_path: Path to package (if already on nodes)
        package_url: URL to download package from
        parallel_download: Download packages in parallel
        **kwargs: Additional arguments for remote_upgrade_node

    Returns:
        Dict mapping node host to success status
    """
    if not SSH_AVAILABLE:
        logging.error("[FAIL] SSH support not available. Install paramiko: pip install paramiko")
        return {}

    results = {}

    # Download packages if URL provided
    if package_url:
        logging.info(f"\n{'='*70}")
        logging.info("Downloading Packages to Nodes")
        logging.info(f"{'='*70}\n")

        if parallel_download:
            # Parallel download using threads
            download_threads = []
            download_results = {}

            def download_thread(node_config):
                host = node_config['host']
                try:
                    with SSHNodeManager(**node_config) as node:
                        remote_pkg = download_package_to_node(node, package_url)
                        download_results[host] = remote_pkg
                except Exception as e:
                    logging.error(f"Download failed for {host}: {e}")
                    download_results[host] = None

            for node_config in nodes:
                thread = threading.Thread(target=download_thread, args=(node_config,))
                thread.start()
                download_threads.append(thread)

            # Wait for all downloads
            for thread in download_threads:
                thread.join()

            # Update package_path for each node
            node_packages = download_results
        else:
            # Sequential download
            node_packages = {}
            for node_config in nodes:
                host = node_config['host']
                try:
                    with SSHNodeManager(**node_config) as node:
                        remote_pkg = download_package_to_node(node, package_url)
                        node_packages[host] = remote_pkg
                except Exception as e:
                    logging.error(f"Download failed for {host}: {e}")
                    node_packages[host] = None
    else:
        # Use provided package_path for all nodes
        node_packages = {node['host']: package_path for node in nodes}

    # Upgrade nodes sequentially
    logging.info(f"\n{'='*70}")
    logging.info("Upgrading Cluster Nodes (Sequential)")
    logging.info(f"{'='*70}\n")

    for node_config in nodes:
        host = node_config['host']
        pkg_path = node_packages.get(host)

        if not pkg_path:
            logging.error(f"[FAIL] No package available for {host}, skipping")
            results[host] = False
            continue

        try:
            with SSHNodeManager(**node_config) as node:
                success = remote_upgrade_node(node, pkg_path, **kwargs)
                results[host] = success

                if not success:
                    logging.error(f"[FAIL] Upgrade failed for {host}")

                    # Ask user if they want to continue
                    response = input(f"\nContinue with remaining nodes? (yes/no): ").strip().lower()
                    if response not in ['yes', 'y']:
                        logging.info("Cluster upgrade aborted by user")
                        break
        except Exception as e:
            logging.error(f"[FAIL] Error upgrading {host}: {e}")
            results[host] = False

    # Summary
    logging.info(f"\n{'='*70}")
    logging.info("Cluster Upgrade Summary")
    logging.info(f"{'='*70}\n")

    success_count = sum(1 for v in results.values() if v)
    total_count = len(results)

    for host, success in results.items():
        status = "[OK]" if success else "[FAIL]"
        logging.info(f"  {status} {host}")

    logging.info(f"\nTotal: {success_count}/{total_count} nodes upgraded successfully\n")

    return results

    return all_passed



def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Striim Platform Upgrade Helper',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check current version
  python3 striim_upgrade_helper.py --check-version

  # Full upgrade - Interactive
  sudo python3 striim_upgrade_helper.py --package striim-node-5.4.0.2-Linux.rpm

  # Full upgrade - Automated
  sudo python3 striim_upgrade_helper.py --package striim-node-5.4.0.2-Linux.rpm --auto

  # Dry-run
  sudo python3 striim_upgrade_helper.py --package striim-node-5.4.0.2-Linux.rpm --dry-run

  # Rollback to previous version
  sudo python3 striim_upgrade_helper.py --rollback /opt/Striim_5_2_0_4
        """
    )

    # Package options
    parser.add_argument('--package', help='Path to Striim package (.rpm or .deb)')

    # Striim paths
    parser.add_argument('--striim-home', default=DEFAULT_STRIIM_HOME,
                       help=f'Striim installation directory (default: {DEFAULT_STRIIM_HOME})')
    parser.add_argument('--backup-dir', default=DEFAULT_BACKUP_BASE_DIR,
                       help=f'Backup directory (default: {DEFAULT_BACKUP_BASE_DIR})')

    # Behavior options
    parser.add_argument('--auto', action='store_true',
                       help='Automated mode (no prompts)')
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be done without executing')
    parser.add_argument('--skip-java-check', action='store_true',
                       help='Skip Java 17 verification')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose logging')
    parser.add_argument('--no-rollback', action='store_true',
                       help='Disable automatic rollback on failure')

    # Utility commands
    parser.add_argument('--check-version', action='store_true',
                       help='Check current Striim version and exit')
    parser.add_argument('--rollback', metavar='BACKUP_DIR',
                       help='Rollback to version from backup directory')

    # Remote mode (Phase 3)
    parser.add_argument('--nodes', metavar='HOST', nargs='+',
                       help='Remote nodes to upgrade (enables SSH mode)')
    parser.add_argument('--ssh-user', default='striim',
                       help='SSH username (default: striim)')
    parser.add_argument('--ssh-password',
                       help='SSH password (optional if using --ssh-key)')
    parser.add_argument('--ssh-key',
                       help='Path to SSH private key (optional if using --ssh-password)')
    parser.add_argument('--ssh-port', type=int, default=22,
                       help='SSH port (default: 22)')
    parser.add_argument('--package-url',
                       help='URL to download Striim package from (for remote mode)')
    parser.add_argument('--parallel-download', action='store_true',
                       help='Download packages to nodes in parallel (remote mode only)')

    args = parser.parse_args()

    # Setup logging
    setup_logging(verbose=args.verbose)

    # Handle utility commands
    if args.check_version:
        check_version_command(args.striim_home)
        return 0

    if args.rollback:
        rollback_command(args.rollback, args.striim_home)
        return 0

    # Remote mode (Phase 3)
    if args.nodes:
        if not SSH_AVAILABLE:
            logging.error("[FAIL] SSH support not available. Install paramiko:")
            logging.error("  pip install paramiko\n")
            return 1

        # Require either package or package-url
        if not args.package and not args.package_url:
            logging.error("[FAIL] Either --package or --package-url is required for remote upgrade\n")
            return 1

        # Require SSH credentials
        if not args.ssh_password and not args.ssh_key:
            logging.error("[FAIL] Either --ssh-password or --ssh-key is required for remote mode\n")
            return 1

        # Build node configurations
        node_configs = []
        for host in args.nodes:
            node_config = {
                'host': host,
                'username': args.ssh_user,
                'port': args.ssh_port
            }
            if args.ssh_password:
                node_config['password'] = args.ssh_password
            if args.ssh_key:
                node_config['key_file'] = args.ssh_key
            node_configs.append(node_config)

        # Run cluster upgrade
        logging.info("=" * 70)
        logging.info("Striim Platform Upgrade Helper - Phase 3 (Remote Mode)")
        logging.info("=" * 70)
        logging.info(f"\nUpgrading {len(node_configs)} node(s):")
        for config in node_configs:
            logging.info(f"  - {config['host']}")
        logging.info("")

        results = upgrade_cluster(
            nodes=node_configs,
            package_path=args.package,
            package_url=args.package_url,
            parallel_download=args.parallel_download,
            striim_home=args.striim_home,
            backup_dir=args.backup_dir,
            skip_java=args.skip_java_check,
            auto_rollback=not args.no_rollback
        )

        # Return success if all nodes upgraded successfully
        success_count = sum(1 for v in results.values() if v)
        if success_count == len(results):
            logging.info("\n[OK] All nodes upgraded successfully!\n")
            return 0
        else:
            logging.error(f"\n[FAIL] {len(results) - success_count} node(s) failed to upgrade\n")
            return 1

    # Main upgrade workflow (Local mode)
    logging.info("=" * 70)
    logging.info("Striim Platform Upgrade Helper - Phase 2 (Full Upgrade)")
    logging.info("=" * 70)
    logging.info("")

    # Require package for upgrade
    if not args.package:
        logging.error("[FAIL] --package is required for upgrade\n")
        logging.info("Usage: sudo python3 striim_upgrade_helper.py --package <path-to-rpm-or-deb>\n")
        return 1

    # Run pre-flight checks
    if not run_preflight_checks(args.striim_home, skip_java=args.skip_java_check):
        logging.error("\n[FAIL] Pre-flight checks failed. Please fix issues and try again.\n")
        return 1

    # Get current version and OS type
    try:
        version_info = get_striim_version(args.striim_home)
        current_version = version_info['version']
        os_type = detect_os_type()
    except Exception as e:
        logging.error(f"Failed to detect system information: {e}")
        return 1

    # Dry-run mode
    if args.dry_run:
        logging.info("=== DRY-RUN MODE ===\n")
        logging.info("Would perform the following actions:")
        logging.info(f"1. Backup Striim {current_version}")
        logging.info(f"   - Version backup: /opt/{generate_backup_name(current_version)}")
        logging.info(f"   - Timestamped backup: {args.backup_dir}/backup-<timestamp>")
        logging.info(f"2. Stop Striim service")
        logging.info(f"3. Uninstall current version")
        logging.info(f"4. Install new version from: {args.package}")
        logging.info(f"5. Restore configuration")
        logging.info(f"6. Run metadata upgrade")
        logging.info(f"7. Start Striim service")
        logging.info(f"8. Verify upgrade")
        logging.info("\n[INFO] Dry-run complete. No changes made.\n")
        return 0

    # User preparation prompts
    if not prompt_user_preparation(auto_mode=args.auto):
        return 1

    # Check for upgrade manager integration
    check_upgrade_manager_integration(auto_mode=args.auto)

    # Perform backup
    try:
        backup_info = backup_striim(args.striim_home, args.backup_dir, current_version)
        logging.info(f"Backup successful!")
        logging.info(f"  Version backup: {backup_info['version_backup']}")
        logging.info(f"  Timestamped backup: {backup_info['timestamped_backup']}\n")
    except Exception as e:
        logging.error(f"\n[FAIL] Backup failed: {e}\n")
        return 1

    # Perform upgrade
    auto_rollback = not args.no_rollback
    if not perform_upgrade(args.striim_home, args.package, backup_info, os_type, auto_rollback):
        logging.error("\n[FAIL] Upgrade failed\n")
        return 1

    # Start Striim
    logging.info("Starting Striim service...")
    if not start_striim_service():
        logging.error("[FAIL] Could not start Striim\n")
        logging.info(f"You can try to rollback: sudo python3 {sys.argv[0]} --rollback {backup_info['version_backup']}\n")
        return 1

    # Wait for startup
    if not wait_for_striim_startup():
        logging.error("[FAIL] Striim did not start properly\n")
        logging.info(f"You can try to rollback: sudo python3 {sys.argv[0]} --rollback {backup_info['version_backup']}\n")
        return 1

    # Verify upgrade
    if not verify_upgrade(args.striim_home):
        logging.warning("\n[WARN] Verification found issues\n")
        logging.info(f"You can rollback if needed: sudo python3 {sys.argv[0]} --rollback {backup_info['version_backup']}\n")

    # Success!
    logging.info("=" * 70)
    logging.info("Upgrade Complete!")
    logging.info("=" * 70)
    logging.info("")
    logging.info("Next steps:")
    logging.info("1. Verify applications are working correctly")
    logging.info("2. If you used striim_upgrade_manager.py, restore components:")
    logging.info("   python3 striim_upgrade_manager.py --load-components")
    logging.info("   python3 striim_upgrade_manager.py --restore-to-apps")
    logging.info("   python3 striim_upgrade_manager.py --restore-app-states")
    logging.info("3. Upgrade Forwarding Agents on each host")
    logging.info("4. Redeploy and start applications")
    logging.info("")
    logging.info(f"Backup location (for rollback): {backup_info['version_backup']}")
    logging.info("")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logging.info("\n\n[INFO] Interrupted by user\n")
        sys.exit(130)
    except Exception as e:
        logging.error(f"\n[FAIL] Unexpected error: {e}\n")
        sys.exit(1)




def check_java_version(required_major: int = 17) -> Tuple[bool, str]:
    """
    Check if Java is installed and meets version requirements

    Args:
        required_major: Required Java major version (default: 17)

    Returns:
        Tuple of (is_valid, version_string)
    """
    try:
        result = subprocess.run(
            ['java', '-version'],
            capture_output=True,
            text=True,
            timeout=5
        )

        # Java version is in stderr
        output = result.stderr

        # Parse version (e.g., "openjdk version \"17.0.2\"")
        match = re.search(r'version "(\d+)\.', output)
        if match:
            major_version = int(match.group(1))
            return (major_version >= required_major, output.split('\n')[0])

        return (False, "Could not parse Java version")

    except (subprocess.TimeoutExpired, FileNotFoundError):
        return (False, "Java not found")

