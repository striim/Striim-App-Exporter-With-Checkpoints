# Striim Upgrade Manager

Automates the removal and restoration of Open Processors (OPs) and User-Defined Functions (UDFs) during Striim platform upgrades.

## Problem Statement

When upgrading Striim from one version to another, custom components (OPs and UDFs) must be:
1. Removed from applications before upgrade
2. Unloaded from the platform
3. Recompiled for the new version
4. Loaded back into the platform
5. Restored to applications
6. Applications redeployed to their original deployment groups and states

This tool automates this entire workflow and maintains state across the upgrade process, including tracking deployment groups and strategies (ON_ONE vs ON_ALL).

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
   # Full analysis (exports and analyzes)
   python3 striim_upgrade_manager.py --analyze

   # Re-analyze from existing exported files (no re-export)
   python3 striim_upgrade_manager.py --analyze-from-files
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

7. **Restore App States** - Returns applications to their original states and deployment groups:
   ```sql
   DEPLOY APPLICATION <name> ON {ONE|ALL} IN <deployment_group>;  -- For apps that were DEPLOYED
   START APPLICATION <name>;   -- For apps that were RUNNING
   ```
   ```bash
   python3 striim_upgrade_manager.py --restore-app-states
   ```

   The tool automatically restores:
   - **Deployment strategy**: `ON_ONE` (single server) or `ON_ALL` (all servers in group)
   - **Deployment group**: The specific deployment group the app was running in
   - **Application state**: DEPLOYED or RUNNING

## Command Reference

### Actions

| Flag | Description |
|------|-------------|
| `--analyze` | Scan all apps for OPs/UDFs and capture application states (exports and analyzes) |
| `--analyze-from-files` | Re-analyze from existing exported files (no re-export) |
| `--remove-from-apps` | Remove components from apps (ALTER, DROP, RECOMPILE) |
| `--unload-components` | Unload components from Striim |
| `--load-components` | Load new components (requires `--component-path`) |
| `--restore-to-apps` | Restore components to apps (ALTER, CREATE, RECOMPILE) |
| `--restore-app-states` | Restore applications to their original states and deployment groups |
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
- **Deployment plans** (deployment group and strategy: ON_ONE/ON_ALL)
- Which components have been removed
- Which components have been unloaded
- Which components have been loaded
- Which apps have been restored

This allows the upgrade process to be interrupted and resumed, and ensures applications are restored to their exact original deployment configuration.

## Deployment Groups and Strategies

The tool automatically tracks and restores deployment configurations, including **multi-flow applications** where different flows run on different deployment groups (e.g., Forwarding Agents).

### Deployment Strategies

- **ON_ONE**: Application/flow runs on a single server in the deployment group (Striim picks the least loaded)
- **ON_ALL**: Application/flow runs on all servers in the deployment group

### Deployment Groups

Deployment groups control which servers/agents run specific applications or flows. Common groups:
- **default**: The default deployment group containing all servers
- **Custom groups**: User-defined groups for specific servers or Forwarding Agents

### Multi-Flow Applications

Striim applications can contain multiple flows, each deployed to different deployment groups. This is common when using Forwarding Agents:

```sql
CREATE APPLICATION MyApp;

CREATE FLOW AgentFlow;
  -- Source reading local files on agent
  CREATE SOURCE ... USING FileReader ...
END FLOW AgentFlow;

CREATE FLOW ServerFlow;
  -- Target writing to database on server
  CREATE TARGET ... USING DatabaseWriter ...
END FLOW ServerFlow;

END APPLICATION MyApp;

-- Deploy with different groups per flow
DEPLOY APPLICATION MyApp ON ONE IN default
WITH AgentFlow ON ALL IN agent_group,
     ServerFlow ON ONE IN default;
```

### How It Works

1. **During Analysis**: The tool runs `DESCRIBE APPLICATION` for each deployed/running app to capture:
   - Application-level deployment strategy and group
   - Per-flow deployment strategies and groups (for multi-flow apps)

2. **During Restore**: The tool reconstructs the exact deployment command:
   ```sql
   -- Single-flow app
   DEPLOY APPLICATION admin.SimpleApp ON ONE IN default;

   -- Multi-flow app
   DEPLOY APPLICATION admin.MultiFlowApp ON ONE IN default
   WITH AgentFlow ON ALL IN agent_group,
        ServerFlow ON ONE IN default;
   ```

3. **Example State**:

   **Single-flow application:**
   ```json
   {
     "deployment_plans": {
       "admin.SimpleApp": {
         "application": {
           "strategy": "ON_ONE",
           "deploymentGroup": "default"
         },
         "flows": {}
       }
     }
   }
   ```

   **Multi-flow application:**
   ```json
   {
     "deployment_plans": {
       "admin.MultiFlowApp": {
         "application": {
           "strategy": "ON_ONE",
           "deploymentGroup": "default"
         },
         "flows": {
           "AgentFlow": {
             "strategy": "ON_ALL",
             "deploymentGroup": "agent_group"
           },
           "ServerFlow": {
             "strategy": "ON_ONE",
             "deploymentGroup": "default"
           }
         }
       }
     }
   }
   ```

This ensures that after upgrade, applications return to the exact same servers/agents they were running on before, with each flow deployed to its correct deployment group.

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

