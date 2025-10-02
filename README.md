# Striim Export All with Checkpoint Position Updater

This tool automatically exports all Striim applications using bulk export, retrieves their checkpoint history, and updates TQL files with the latest checkpoint positions for recovery purposes.

## Features

- **Bulk Export**: Uses `EXPORT APPLICATION ALL` with passphrase protection
- **Multi-Database Support**: Handles MySQL, SQL Server, MongoDB, and Oracle sources
  - MySQL (`Global.MysqlReader`)
  - SQL Server (`Global.MSSqlReader`, `Global.MSJet`)
  - MongoDB (`Global.MongoDBReader`)
  - Oracle (`Global.OracleReader`, `Global.OJet`)
- **Automatic Discovery**: Uses `mon;` command to discover all applications
- **Smart Position Updates**:
  - MySQL: Updates `StartTimestamp` with `FileName:binlog;offset:position`
  - SQL Server/MSJet: Updates `StartPosition` with `LSN:0xHEXVALUE` (prioritizes LSN)
  - MongoDB: Adds/updates `startTimestamp` with `YYYY-MM-DDTHH:MM:SS` format
  - Oracle/OJet: Adds/updates `startSCN` with SCN number from checkpoint
- **Environment Configuration**: Support for multiple environments (dev, staging, prod)
- **Flexible Options**: Configurable overwrite behavior, custom passphrases, etc.

## Files

- `striim_export_all_with_checkpoint.py` - Export and checkpoint update script
- `striim_import_apps.py` - Import applications script
- `config.py` - Configuration file with environment settings
- `README.md` - This documentation

## Configuration

### Default Settings (config.py)

```python
STRIIM_CONFIG = {
    'url': 'http://localhost:9080',
    'username': 'admin', 
    'password': 'admin',
    'passphrase': 'striim123',
}
```

### Environment-Specific Settings

The tool supports multiple environments:

- **default**: Uses `STRIIM_CONFIG` values
- **development**: Development environment settings
- **staging**: Staging environment settings  
- **production**: Production environment settings

## Usage

### Basic Usage

```bash
# Use default configuration
python3 striim_export_all_with_checkpoint.py

# Skip overwriting existing files
python3 striim_export_all_with_checkpoint.py --no-overwrite
```

### Environment-Specific Usage

```bash
# Use development environment
python3 striim_export_all_with_checkpoint.py --environment development

# Use production environment
python3 striim_export_all_with_checkpoint.py --environment production
```

### Custom Parameters

```bash
# Override specific settings
python3 striim_export_all_with_checkpoint.py \
  --url http://my-striim:9080 \
  --username myuser \
  --password mypass \
  --passphrase "myCustomPass123"

# Custom stage directory
python3 striim_export_all_with_checkpoint.py --stage-dir /path/to/exports
```

### Command Line Options

```
--stage-dir STAGE_DIR     Directory for exported applications (default: stage)
--url URL                 Striim server URL (default: from config)
--username USERNAME       Striim username (default: from config)
--password PASSWORD       Striim password (default: from config)
--passphrase PASSPHRASE   Export encryption passphrase (default: from config)
--environment ENV         Environment config (default|development|staging|production)
--no-overwrite           Skip export if TQL files already exist
```

## How It Works

1. **Authentication**: Connects to Striim API using configured credentials
2. **Discovery**: Runs `mon;` command to get list of all applications
3. **Bulk Export**: Executes `EXPORT APPLICATION ALL passphrase="<passphrase>";`
4. **Extraction**: Extracts individual TQL files from the encrypted zip
5. **Checkpoint Processing**: For each application:
   - Checks if it uses supported readers (`Global.MysqlReader`, `Global.MSSqlReader`, `Global.MSJet`, `Global.MongoDBReader`, `Global.OracleReader`, or `Global.OJet`)
   - Runs `SHOW <app> CHECKPOINT HISTORY;` to get latest position
   - Updates TQL file with checkpoint position if available
   - For MongoDB: Adds `startTimestamp` field if it doesn't exist
   - For Oracle/OJet: Adds `startSCN` field if it doesn't exist

## Output

The tool creates:

- `stage/all_applications.zip` - Encrypted export of all applications
- `stage/<app_name>.tql` - Individual TQL files for each application
- Updated TQL files with checkpoint positions for recovery

## Example Output

```
🚀 Starting Striim Export All with Checkpoint Position Updater
   Striim URL: http://localhost:9080
   Stage Directory: stage

Step 1: Authenticating with Striim...
✓ Authentication successful. Token: abc123...

Step 2: Getting application list...
✓ Found 3 applications: admin.SQLIL, admin.mysqlcdc, admin.SQLCDCTest

Step 3: Exporting all applications...
✓ Successfully exported all applications to stage/all_applications.zip
✓ Successfully extracted 3 out of 3 applications

Step 4: Processing applications for checkpoint updates...
  Processing admin.mysqlcdc...
    ✓ Uses Global.MysqlReader
    ✓ Found checkpoint: FileName:ON.000003;offset:3254
    ✓ Updated TQL file with position: FileName:ON.000003;offset:3254

  Processing admin.mgo...
    ✓ Uses Global.MongoDBReader
    ✓ Found checkpoint: 2025-10-02T20:51:55
    ✓ Updated TQL file with position: 2025-10-02T20:51:55

  Processing admin.OrcTest...
    ✓ Uses Global.OracleReader
    ✓ Found checkpoint: 30507693
    ✓ Updated TQL file with position: 30507693

🎉 Processing complete!
   Using Global.MysqlReader: 1
   Using Global.MSSqlReader: 1
   Using Global.MongoDBReader: 1
   Using Global.OracleReader: 1
   Updated with positions: 4
```

## Customizing Configuration

Edit `config.py` to modify:

- Server URLs and credentials for different environments
- Default passphrase for exports
- Stage directory location
- Supported reader types

## Requirements

- Python 3.6+
- `requests` library
- Access to Striim server with admin privileges

---

# Striim Application Importer

## Overview

The `striim_import_apps.py` script imports TQL files from the `import/` directory to a Striim server.

## Features

- **Bulk Import**: Imports all TQL files from a directory
- **Force Mode**: Automatically stops, undeploys, and drops existing applications before importing
- **Auto Deploy**: Optionally deploys applications after importing
- **Namespace Support**: Automatically extracts namespace from filename (e.g., `admin.mgo.tql` → `admin.mgo`)
- **Environment Configuration**: Uses separate import configuration from `config.py`

## Usage

### Basic Import

```bash
# Import all TQL files from import/ directory
python3 striim_import_apps.py

# Import with force mode (removes existing apps first)
python3 striim_import_apps.py --force

# Import and deploy applications
python3 striim_import_apps.py --force --deploy
```

### Environment-Specific Import

```bash
# Import to production environment
python3 striim_import_apps.py --environment production --force --deploy

# Import to staging environment
python3 striim_import_apps.py --environment staging --force
```

### Custom Parameters

```bash
# Override specific settings
python3 striim_import_apps.py \
  --url http://target-server:9080 \
  --username admin \
  --password mypass \
  --passphrase "import123" \
  --force --deploy

# Custom import directory
python3 striim_import_apps.py --import-dir /path/to/tql/files --force
```

### Command Line Options

```
--import-dir DIR      Directory containing TQL files (default: import)
--url URL             Striim server URL (default: from config)
--username USERNAME   Striim username (default: from config)
--password PASSWORD   Striim password (default: from config)
--passphrase PASS     Import passphrase (default: from config)
--deploy              Deploy applications after importing
--force               Stop, undeploy, and drop existing apps before import
--environment ENV     Environment config (default|development|staging|production)
```

## How It Works

1. **Scan Directory**: Finds all `.tql` files in the import directory
2. **Authentication**: Connects to Striim API using configured credentials
3. **For Each TQL File**:
   - Extracts application name and namespace from filename
   - If `--force`: Stops → Undeploys → Drops existing application
   - Imports TQL content via API with passphrase
   - If `--deploy`: Deploys the imported application
4. **Summary**: Reports success/failure counts

## Example Workflow

```bash
# Step 1: Export applications with checkpoints from source server
python3 striim_export_all_with_checkpoint.py

# Step 2: Copy updated TQL files to import directory
cp stage/*.tql import/

# Step 3: Import to target server with force and deploy
python3 striim_import_apps.py --force --deploy
```

## Configuration

The import script uses `STRIIM_CONFIG_IMPORT` from `config.py`:

```python
STRIIM_CONFIG_IMPORT = {
    'url': 'http://localhost:9080',
    'username': 'admin',
    'password': 'admin',
    'passphrase': 'striim123',
}
```

## Notes

- **Filename Convention**: Use `namespace.appname.tql` format (e.g., `admin.mgo.tql`)
- **Force Mode**: Required when applications already exist on target server
- **Passphrase**: Must match the passphrase used during export (if applicable)
- **Deploy Order**: Applications are imported/deployed in alphabetical order
