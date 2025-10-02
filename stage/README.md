# Stage Directory

## Purpose

This directory is used by `striim_export_all_with_checkpoint.py` to store exported Striim applications.

## Contents

When you run the export script, this directory will contain:

- **`all_applications.zip`** - Encrypted zip file containing all exported applications (passphrase-protected)
- **`<namespace>.<appname>.tql`** - Individual TQL files for each application, extracted from the zip
  - Example: `admin.mysqlcdc.tql`, `admin.OrcTest.tql`, etc.

## Checkpoint Updates

The export script automatically updates TQL files in this directory with checkpoint positions for disaster recovery:

- **MySQL** (`Global.MysqlReader`) - Updates `StartTimestamp` with binlog position
- **SQL Server** (`Global.MSSqlReader`, `Global.MSJet`) - Updates `StartPosition` with LSN
- **MongoDB** (`Global.MongoDBReader`) - Adds/updates `startTimestamp` with timestamp
- **Oracle** (`Global.OracleReader`, `Global.OJet`) - Adds/updates `startSCN` with SCN number

## Usage

1. Run the export script:
   ```bash
   python3 striim_export_all_with_checkpoint.py
   ```

2. The script will:
   - Export all applications to `all_applications.zip`
   - Extract individual TQL files
   - Update TQL files with checkpoint positions (if available)

3. Use these TQL files for:
   - Disaster recovery
   - Migration to another Striim server
   - Version control / backup
   - Importing to another environment

## Notes

- This directory is automatically created if it doesn't exist
- Files in this directory are overwritten on each export
- The TQL files contain the complete application definitions with updated checkpoint positions

