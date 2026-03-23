# Striim Upgrade Manager

Automates the removal and restoration of Open Processors (OPs) and User-Defined Functions (UDFs) during Striim platform upgrades.

## Problem Statement

When upgrading Striim from one version to another, custom components (OPs and UDFs) must be:
1. Removed from applications before upgrade
2. Unloaded from the platform
3. Recompiled for the new version
4. Loaded back into the platform
5. Restored to applications

This tool automates this entire workflow and maintains state across the upgrade process.

## Components

- **`striim_upgrade_manager.py`** - Core Python automation script
- **`upgrade_wizard.sh`** - Interactive shell interface
- **`upgrade_state.json`** - State persistence file (auto-generated)

## Prerequisites

- Python 3.6+
- `requests` library (`pip install requests`)
- Access to Striim API (configured in `config.py`)
- Striim admin credentials

## Quick Start

### Option 1: Interactive Wizard (Recommended)

```bash
./upgrade_wizard.sh
```

Follow the menu prompts to execute each step.

### Option 2: Command Line

```bash
# Step 1: Analyze applications
python3 striim_upgrade_manager.py --analyze

# Step 2: Prepare for upgrade (runs analyze, remove, unload)
python3 striim_upgrade_manager.py --prepare-for-upgrade

# [Upgrade Striim here]

# Step 3: Load new components
python3 striim_upgrade_manager.py --load-components --component-path UploadedFiles/MyOP.scm

# Step 4: Restore to applications
python3 striim_upgrade_manager.py --restore-to-apps
```

## Upgrade Workflow

### Pre-Upgrade Steps

1. **Analyze** - Scans all applications to find OPs/UDFs and captures application states (RUNNING/DEPLOYED/CREATED)
   ```bash
   python3 striim_upgrade_manager.py --analyze
   ```

   Quick check without full analysis:
   ```bash
   python3 striim_upgrade_manager.py --dry-run --analyze
   ```

2. **Remove from Apps** - Executes for each app with components:
   ```sql
   UNDEPLOY APPLICATION <name>;
   ALTER APPLICATION <name>;
   DROP <namespace>.<component>;  -- Just DROP with qualified name
   ALTER APPLICATION <name> RECOMPILE;
   ```
   ```bash
   python3 striim_upgrade_manager.py --remove-from-apps
   ```

3. **Unload Components** - Removes components from Striim:
   ```sql
   UNLOAD OPEN PROCESSOR '<path>';  -- or UNLOAD UDF
   ```
   ```bash
   python3 striim_upgrade_manager.py --unload-components
   ```

### Upgrade Striim

At this point, upgrade your Striim platform to the new version.

### Post-Upgrade Steps

4. **Upload New Components** - Use Striim UI to upload recompiled `.scm` or `.jar` files to `UploadedFiles/`

5. **Load Components** - Load each component:
   ```bash
   python3 striim_upgrade_manager.py --load-components --component-path UploadedFiles/MyOP.scm
   ```

6. **Restore to Apps** - Adds components back to applications:
   ```sql
   ALTER APPLICATION <name>;
   CREATE OPEN PROCESSOR <name> USING <adapter> (...);
   ALTER APPLICATION <name> RECOMPILE;
   ```
   ```bash
   python3 striim_upgrade_manager.py --restore-to-apps
   ```

7. **Restore App States** - Returns applications to their original states:
   ```sql
   DEPLOY APPLICATION <name>;  -- For apps that were DEPLOYED
   START APPLICATION <name>;   -- For apps that were RUNNING
   ```
   ```bash
   python3 striim_upgrade_manager.py --restore-app-states
   ```

## Command Reference

### Actions

| Flag | Description |
|------|-------------|
| `--analyze` | Scan all apps for OPs/UDFs and capture application states |
| `--remove-from-apps` | Remove components from apps (ALTER, DROP, RECOMPILE) |
| `--unload-components` | Unload components from Striim |
| `--load-components` | Load new components (requires `--component-path`) |
| `--restore-to-apps` | Restore components to apps (ALTER, CREATE, RECOMPILE) |
| `--restore-app-states` | Restore applications to their original states (DEPLOYED/RUNNING) |
| `--prepare-for-upgrade` | Run all pre-upgrade steps (analyze, remove, unload) |
| `--complete-upgrade` | Show post-upgrade instructions |
| `--status` | Display current upgrade state |
| `--reset-state` | Reset state file (creates backup) |

### Options

| Flag | Description |
|------|-------------|
| `--dry-run` | Show what would be done without executing |
| `--component-path <path>` | Path to component file (e.g., `UploadedFiles/MyOP.scm`) |
| `--component-type <OP\|UDF>` | Component type (for reference) |

## Examples

### Dry-Run Mode

Test what would happen without making changes:

```bash
# Quick check: view app states without full analysis
python3 striim_upgrade_manager.py --dry-run --analyze

# Preview any action before executing
python3 striim_upgrade_manager.py --dry-run --remove-from-apps
python3 striim_upgrade_manager.py --dry-run --restore-app-states
```

### Check Status

View current upgrade state:

```bash
python3 striim_upgrade_manager.py --status
```

Output:
```json
{
  "phase": "components_removed",
  "timestamp": "2024-03-22T19:30:00",
  "apps_with_components": {
    "admin.MyApp": [
      {
        "type": "OP",
        "name": "admin.MyProcessor",
        "create_statement": "CREATE OPEN PROCESSOR admin.MyProcessor USING MyAdapter (...);",
        "namespace": "admin",
        "app_name": "MyApp"
      }
    ]
  },
  "removed_components": {...},
  "unloaded_components": [...],
  "loaded_components": [...],
  "restored_apps": [...]
}
```

### Reset State

If you need to start over:

```bash
python3 striim_upgrade_manager.py --reset-state
```

This creates a backup (`upgrade_state.json.backup.YYYYMMDD_HHMMSS`) before resetting.

## State Management

The tool maintains state in `upgrade_state.json` to track:
- Which applications have OPs/UDFs (including CQs with custom UDFs)
- Original CREATE statements for each component
- Application states (RUNNING/DEPLOYED/CREATED)
- Which components have been removed
- Which components have been unloaded
- Which components have been loaded
- Which apps have been restored

This allows the upgrade process to be interrupted and resumed.

## Troubleshooting

### Authentication Failed

Check `config.py` settings:
```python
{
    'url': 'https://localhost:9081',
    'username': 'admin',
    'password': 'admin',
    'passphrase': 'striim123'
}
```

### Component Not Found

Ensure the component path is correct:
- Use `UploadedFiles/` prefix
- Include file extension (`.scm` or `.jar`)
- Upload file via Striim UI first

### Application Won't Recompile

Check Striim logs for compilation errors. The component may need code changes for the new version.

## Safety Features

- **Dry-run mode** - Test without making changes
- **State persistence** - Resume if interrupted
- **Backup on reset** - State file backed up before deletion
- **Confirmation prompts** - Interactive wizard asks for confirmation on destructive operations

## Next Steps

After completing the upgrade:
1. Restore application states (automatically returns apps to DEPLOYED/RUNNING)
2. Verify component functionality
3. Monitor for errors
4. Keep `upgrade_state.json` as a record

## Support

For issues or questions, refer to:
- Striim TQL documentation: `guidebook/appendix/A.2-tql-commands.md`
- Striim API reference: `guidebook/appendix/A.6-api-reference.md`

