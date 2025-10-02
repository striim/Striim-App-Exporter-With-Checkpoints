# Import Directory

## Purpose

This directory is used by `striim_import_apps.py` to import Striim applications from TQL files.

## Contents

Place TQL files in this directory that you want to import to a Striim server:

- **`<namespace>.<appname>.tql`** - TQL files to be imported
  - Example: `admin.mysqlcdc.tql`, `admin.OrcTest.tql`, etc.
  - The filename should follow the pattern: `<namespace>.<appname>.tql`
  - The namespace is extracted from the filename for proper application management

## Usage

### Basic Import

1. Copy TQL files to this directory:
   ```bash
   cp /path/to/your/app.tql import/
   ```

2. Run the import script:
   ```bash
   python3 striim_import_apps.py
   ```

### Force Import (Overwrite Existing Apps)

If applications already exist on the target server, use the `--force` flag:

```bash
python3 striim_import_apps.py --force
```

This will:
1. **STOP** the application (if running)
2. **UNDEPLOY** the application
3. **DROP** the application (with CASCADE)
4. **IMPORT** the new TQL file

### Auto-Deploy After Import

To automatically deploy applications after importing:

```bash
python3 striim_import_apps.py --deploy
```

Or combine with force mode:

```bash
python3 striim_import_apps.py --force --deploy
```

## Workflow Example

### Disaster Recovery Scenario

1. **Export from source server** (using export script):
   ```bash
   python3 striim_export_all_with_checkpoint.py
   ```
   - This creates TQL files in `stage/` directory with checkpoint positions

2. **Copy TQL files to import directory**:
   ```bash
   cp stage/*.tql import/
   ```

3. **Import to target server** (using import script):
   ```bash
   python3 striim_import_apps.py --force --deploy
   ```
   - This imports all apps with their checkpoint positions
   - Applications will resume from the last checkpoint position

## Notes

- This directory is automatically created if it doesn't exist
- The import script scans this directory for all `.tql` files
- Files are not deleted after import (you can manually clean up)
- The import server configuration is defined in `config.py` under `STRIIM_CONFIG_IMPORT`
- Import uses passphrase authentication via URL query parameter

