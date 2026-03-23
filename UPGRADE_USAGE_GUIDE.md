# Striim Upgrade Manager - Quick Usage Guide

## What This Tool Does

The Striim Upgrade Manager automates the tedious process of removing and restoring Open Processors (OPs) and User-Defined Functions (UDFs) during Striim platform upgrades.

**Without this tool**, you would need to:
1. Manually identify which applications use OPs/UDFs
2. Manually undeploy each application
3. Manually execute ALTER, DROP, and RECOMPILE commands for each component
4. Keep track of all the original CREATE statements
5. After upgrade, manually recreate everything

**With this tool**, you run:
```bash
python3 striim_upgrade_manager.py --prepare-for-upgrade
# [Upgrade Striim]
python3 striim_upgrade_manager.py --load-components --component-path <path>
python3 striim_upgrade_manager.py --restore-to-apps
```

---

## Quick Start (5 Minutes)

### Step 1: Analyze Your Environment

```bash
cd /Users/danielferrara/Documents/Code/Striim-App-Exporter-With-Checkpoints
python3 striim_upgrade_manager.py --analyze
```

**What it does:**
- Exports all applications
- Scans TQL files for OPs and UDFs
- Saves findings to `upgrade_state.json`

**Example output:**
```
=== Analysis Results ===
Found 3 applications with components:

admin.MonitoringApp:
  - OP: admin.MyStriimWatcher

admin.CDCPipeline:
  - OP: admin.DataEnricher

Total: 2 OPs across 2 applications
```

### Step 2: Check What Was Found

```bash
python3 striim_upgrade_manager.py --status
```

This shows the complete state including all detected components and their CREATE statements.

### Step 3: Test with Dry-Run

```bash
python3 striim_upgrade_manager.py --dry-run --remove-from-apps
```

**What it does:**
- Shows exactly what commands would be executed
- **Does NOT make any changes**
- Lets you verify before proceeding

### Step 4: Prepare for Upgrade

```bash
python3 striim_upgrade_manager.py --prepare-for-upgrade
```

**What it does:**
1. Undeploys applications with OPs/UDFs
2. Removes components from applications (ALTER → DROP → RECOMPILE)
3. Unloads component libraries from Striim

**⚠️ This makes changes! Make sure you've tested with --dry-run first.**

### Step 5: Upgrade Striim

Now perform your Striim upgrade:
1. Stop Striim
2. Upgrade to new version
3. Start Striim
4. Upload new component JARs via UI (Manage Striim → Files)

### Step 6: Load New Components

For each component, run:
```bash
python3 striim_upgrade_manager.py --load-components --component-path UploadedFiles/StriimWatcher-5.4.0.jar
python3 striim_upgrade_manager.py --load-components --component-path UploadedFiles/EventChanger-5.4.0.jar
```

### Step 7: Restore to Applications

```bash
python3 striim_upgrade_manager.py --restore-to-apps
```

**What it does:**
- Recreates all components in their original applications
- Uses the exact CREATE statements from before
- Recompiles each application

---

## Interactive Wizard (Easier for First-Time Users)

If you prefer a menu-driven interface:

```bash
./upgrade_wizard.sh
```

This provides:
- Color-coded output
- Confirmation prompts
- Step-by-step guidance
- Shows equivalent Python commands

---

## Real-World Examples

See `UPGRADE_EXAMPLES.md` for detailed examples showing:
- Sample TQL files with OPs and UDFs
- Exact API commands executed for each phase
- Complete workflow with all API calls
- State file examples

Sample TQL files are in the `examples/` directory:
- `sample_app_with_op.tql` - StriimWatcher example
- `sample_app_with_processor.tql` - EventChanger example
- `sample_app_with_udf.tql` - Custom UDF example

---

## Common Scenarios

### Scenario 1: Just Want to See What's There

```bash
python3 striim_upgrade_manager.py --analyze
python3 striim_upgrade_manager.py --status
```

### Scenario 2: Test Everything First

```bash
python3 striim_upgrade_manager.py --dry-run --analyze
python3 striim_upgrade_manager.py --dry-run --remove-from-apps
python3 striim_upgrade_manager.py --dry-run --unload-components
```

### Scenario 3: Full Automated Upgrade

```bash
# Pre-upgrade
python3 striim_upgrade_manager.py --prepare-for-upgrade

# [Upgrade Striim + upload new JARs]

# Post-upgrade
python3 striim_upgrade_manager.py --load-components --component-path UploadedFiles/MyOP.jar
python3 striim_upgrade_manager.py --restore-to-apps
```

### Scenario 4: Something Went Wrong, Start Over

```bash
python3 striim_upgrade_manager.py --reset-state
```

This creates a backup and resets the state file.

---

## Safety Features

✅ **Dry-run mode** - Test without making changes  
✅ **State persistence** - Resume if interrupted  
✅ **Backup on reset** - State file backed up before deletion  
✅ **Confirmation prompts** - Interactive wizard asks before destructive ops  
✅ **Error handling** - Detailed error messages with traceback  

---

## Troubleshooting

### "Authentication failed"

Check `config.py` settings:
```python
{
    'url': 'https://localhost:9081',
    'username': 'admin',
    'password': 'admin'
}
```

### "No components found"

- Make sure applications are deployed before running --analyze
- Check that OPs/UDFs are actually defined in the TQL (not just loaded globally)

### "Component not found" during load

- Ensure you uploaded the JAR via Striim UI first
- Use the correct path: `UploadedFiles/filename.jar`

---

## Next Steps

1. **Read the examples**: `UPGRADE_EXAMPLES.md`
2. **Review the full docs**: `UPGRADE_MANAGER_README.md`
3. **Check the technical summary**: `UPGRADE_MANAGER_SUMMARY.md`
4. **Run analyze on your environment**
5. **Test with dry-run**
6. **Execute the upgrade**

---

## Support

For questions or issues:
- Review the examples in `UPGRADE_EXAMPLES.md`
- Check the full documentation in `UPGRADE_MANAGER_README.md`
- Examine the state file: `python3 striim_upgrade_manager.py --status`

