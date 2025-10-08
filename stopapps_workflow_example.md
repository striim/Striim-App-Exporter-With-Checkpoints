# Complete Export Workflow with --stopapps

The enhanced export script now supports a complete workflow for application migration and maintenance.

## New --stopapps Flag

The `--stopapps` flag adds the ability to stop and undeploy all applications before exporting, similar to the `--force` functionality in the import script.

### What it does:
1. **Stops all applications** using `STOP APPLICATION {app_name};`
2. **Undeploys all applications** using `UNDEPLOY APPLICATION {app_name};`
3. **Continues on errors** - won't fail if an app is already stopped/undeployed
4. **Provides detailed logging** of each operation

## Usage Examples

### Basic Stop and Export
```bash
# Stop all apps, then export
python3 striim_export_all_with_checkpoint.py --stopapps
```

### Complete Migration Workflow
```bash
# Stop apps, auto-drop types, then export with checkpoints
python3 striim_export_all_with_checkpoint.py --stopapps --droptypes
```

### Production Migration
```bash
# Complete workflow for production environment
python3 striim_export_all_with_checkpoint.py \
  --stopapps \
  --droptypes \
  --environment production \
  --stage-dir /backup/striim-export
```

## Workflow Comparison

### Before (Manual Process)
```bash
# 1. Manually stop applications via console
# 2. Manually undeploy applications via console  
# 3. Run bash script to drop types
./drop_types.sh admin MySource
# 4. Export applications
python3 striim_export_all_with_checkpoint.py
```

### After (Automated Process)
```bash
# Single command does everything
python3 striim_export_all_with_checkpoint.py --stopapps --droptypes
```

## Complete Workflow Steps

When you run with `--stopapps --droptypes`, the script executes:

```
🚀 Starting Striim Export All with Checkpoint Position Updater
   Striim URL: http://localhost:9080
   Stage Directory: stage
   Stop Applications: Yes
   Drop Types Mode: Auto-detection

Step 1: Authenticating with Striim...
✓ Authentication successful

Step 2: Getting application list...
✓ Found 8 applications: admin.mgo, admin.testa, admin.SQLIL, ...

Step 2.3: Stopping and undeploying applications...
Stopping and undeploying 8 applications...

  Processing admin.mgo...
    - Stopping...
    ✓ Stopped successfully
    - Undeploying...
    ✓ Undeployed successfully

  [... continues for all apps ...]

Stop/Undeploy summary:
  Total applications: 8
  Successfully stopped: 8
  Successfully undeployed: 8

Step 2.5: Dropping types...
Using auto-detection mode...
Auto-detecting source components from applications with checkpoint data...
  Detected source component: admin.mgo
  Detected source component: admin.testa
  [... continues ...]

Found 8 unique source components to process

Processing source component: admin.mgo
Looking for types with prefix: admin.mgo_
Found 35 total types
No types found with prefix 'admin.mgo_'
[... continues for each component ...]

Step 3: Exporting all applications...
Exporting all applications to stage/all_applications.zip...
✓ Successfully exported all applications

[... continues with extraction and checkpoint processing ...]

🎉 Processing complete!
```

## Benefits of Combined Workflow

### 1. **Complete Automation**
- No manual console commands needed
- Single command handles entire process
- Consistent execution every time

### 2. **Clean Migration**
- Applications stopped in controlled manner
- Old types cleaned up before export
- Fresh export with latest checkpoints
- Ready for import to target environment

### 3. **Error Resilience**
- Continues even if some operations fail
- Detailed logging of each step
- Non-fatal errors don't stop the process

### 4. **Production Ready**
- Environment-specific configurations
- Comprehensive logging
- Predictable behavior

## Integration with Import Process

The complete migration workflow:

```bash
# Source Environment (Export)
python3 striim_export_all_with_checkpoint.py \
  --stopapps \
  --droptypes \
  --environment production

# Copy files to target environment
cp stage/*.tql /target/import/

# Target Environment (Import)
python3 striim_import_apps.py \
  --force \
  --deploy \
  --environment production
```

This provides a complete, automated migration pipeline from source to target Striim environments.
