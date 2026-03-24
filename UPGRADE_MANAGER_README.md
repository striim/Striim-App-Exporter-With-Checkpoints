# Striim Upgrade Manager

Automates the removal and restoration of Open Processors (OPs), User-Defined Functions (UDFs), and Continuous Queries (CQs) with custom UDFs during Striim platform upgrades.

## Problem Statement

When upgrading Striim from one version to another, custom components (OPs, UDFs, and CQs with UDFs) must be:
1. Removed from applications before upgrade
2. Unloaded from the platform (libraries/JARs)
3. Recompiled for the new version
4. Loaded back into the platform
5. Restored to applications
6. Applications redeployed to their original deployment groups and states

This tool automates this entire workflow and maintains state across the upgrade process, including tracking deployment groups and strategies (ON_ONE vs ON_ALL).

## What Gets Detected and Removed

The tool detects and handles three types of custom components:

1. **Open Processors (OPs)**: Custom sources/processors using `USING <CustomAdapter>`
2. **User-Defined Functions (UDFs)**: Custom functions in CQs (e.g., `com.striim.util.AdvFormat.LowercaseTableName()`)
3. **Continuous Queries (CQs)**: CQs that call custom UDFs

**Important:** CQs are detected and removed if they contain UDF calls, as the UDF libraries need to be upgraded.

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

   **For apps in RUNNING/DEPLOYED/HALTED/TERMINATED states:**
   ```sql
   STOP APPLICATION <name>;        -- Only if RUNNING
   UNDEPLOY APPLICATION <name>;    -- For RUNNING/DEPLOYED/HALTED/TERMINATED
   ALTER APPLICATION <name>;
   DROP <component_type> <name>;   -- e.g., DROP SOURCE, DROP OPEN PROCESSOR, DROP CQ
   ALTER APPLICATION <name> RECOMPILE;
   ```

   **Flow-scoped components** (components inside FLOW blocks):
   ```sql
   ALTER APPLICATION <name>;
   ALTER FLOW <flow_name>;
   DROP <component_type> <name>;
   END FLOW <flow_name>;
   ALTER APPLICATION <name> RECOMPILE;
   ```

   ```bash
   python3 striim_upgrade_manager.py --remove-from-apps
   ```

3. **Unload Components** - Unloads ALL libraries from `LIST LIBRARIES`:

   For each library, tries both commands:
   ```sql
   UNLOAD 'UploadedFiles/<filename>';                    -- For UDFs (tries first)
   UNLOAD OPEN PROCESSOR 'UploadedFiles/<filename>';     -- For OPs (tries if first fails)
   ```

   Example output:
   ```
   Unloading 'UploadedFiles/AdvFormat-5.0.2.jar'...
     [OK] Unloaded AdvFormat-5.0.2.jar (UDF)

   Unloading 'UploadedFiles/EventChanger-5.2.0.scm'...
     Trying as Open Processor...
     [OK] Unloaded EventChanger-5.2.0.scm (OP)
   ```

   ```bash
   python3 striim_upgrade_manager.py --unload-components
   ```

### Upgrade Striim

At this point, upgrade your Striim platform to the new version.

### Post-Upgrade Steps

4. **Upload New Components** - Use Striim UI to upload recompiled `.scm` or `.jar` files to `UploadedFiles/`

5. **Load Components** - Load components (auto-detects OP vs UDF):

   **Single file:**
   ```bash
   python3 striim_upgrade_manager.py --load-components --component-path UploadedFiles/MyOP.scm
   ```

   **Multiple files (comma-separated):**
   ```bash
   python3 striim_upgrade_manager.py --load-components --component-path "AdvFormat-5.0.2.jar,EventChanger-5.2.0.scm,SoftDeleteUDF-1.0.0.jar"
   ```

   For each file, the tool automatically:
   1. Tries `LOAD 'UploadedFiles/<filename>';` (for UDFs)
   2. If that fails, tries `LOAD OPEN PROCESSOR 'UploadedFiles/<filename>';` (for OPs)
   3. Reports which type it was (UDF or OP)

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
   DEPLOY APPLICATION <name> ON {ONE|ALL} IN <deployment_group>;  -- For apps that were DEPLOYED/HALTED/TERMINATED
   START APPLICATION <name>;   -- For apps that were RUNNING
   ```
   ```bash
   python3 striim_upgrade_manager.py --restore-app-states
   ```

   The tool automatically restores:
   - **Deployment strategy**: `ON_ONE` (single server) or `ON_ALL` (all servers in group)
   - **Deployment group**: The specific deployment group the app was running in
   - **Application state**:
     - `RUNNING` → Restored to RUNNING (deployed + started)
     - `DEPLOYED` → Restored to DEPLOYED
     - `HALTED` → Restored to DEPLOYED (HALTED apps were undeployed during removal)
     - `TERMINATED` → Restored to DEPLOYED (TERMINATED apps were undeployed during removal)
     - `CREATED` → No action (stays CREATED)

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
- Which applications have OPs/UDFs/CQs (including CQs with custom UDFs)
- Original CREATE statements for each component
- **Flow information** (which FLOW each component belongs to, for correct scoping)
- Application states (RUNNING/DEPLOYED/HALTED/TERMINATED/CREATED)
- **Deployment plans** (deployment group and strategy: ON_ONE/ON_ALL)
- **Library files** (mapping of library base names to full filenames from `LIST LIBRARIES`)
- Which components have been removed
- Which components have been unloaded
- Which components have been loaded
- Which apps have been restored

This allows the upgrade process to be interrupted and resumed, and ensures applications are restored to their exact original deployment configuration.

### Important State Fields

**`library_files`**: Maps library base names to full filenames:
```json
{
  "library_files": {
    "AdvFormat": "AdvFormat-5.0.2.jar",
    "EventChanger": "EventChanger-5.2.0.scm",
    "SoftDeleteUDF": "SoftDeleteUDF-1.0.0.jar"
  }
}
```

**`apps_with_components`**: Tracks components and their flow scope:
```json
{
  "apps_with_components": {
    "DATALAKE.docutech_cdc": [
      {
        "type": "CQ",
        "name": "tablename_lowercase_eq_cdc",
        "flow": "docutech_cdc_transform_flow",
        "component_type": "CQ",
        "udfs": ["com.striim.util.AdvFormat.LowercaseTableName"]
      }
    ]
  }
}
```

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

### Apps Not Being Processed (State = NOT_FOUND)

If you see apps with `NOT_FOUND` state during `--remove-from-apps`:

**Cause:** The apps exist as TQL files but are not currently deployed in Striim, OR you're running the tool on a different Striim system than where the apps were exported from.

**Solution:**
1. **Re-run analyze on the current system:**
   ```bash
   python3 striim_upgrade_manager.py --analyze
   ```
   This will query the current Striim system for app states.

2. **Check if apps are actually deployed:**
   - Run `mon;` in Striim UI to see deployed apps
   - Verify you're connected to the correct Striim instance in `config.py`

### HALTED Apps Not Undeployed

**Cause:** App states were not captured during analyze (shows as `NOT_FOUND` or `UNKNOWN`).

**Solution:** Re-run `--analyze` to capture current app states from Striim.

### Transitional State Errors (STARTING, DEPLOYING, etc.)

If you see errors about apps in transitional states:

**Cause:** Apps are in the process of starting/stopping/deploying when the tool runs.

**Solution:** Wait for apps to reach stable state (RUNNING, DEPLOYED, CREATED) before running the upgrade manager.

### Flow-Scoped Component Removal Issues

If components in FLOW blocks are not being removed correctly:

**Cause:** Flow detection may have failed during analyze.

**Solution:**
1. Check the `upgrade_state.json` for the `flow` field in component entries
2. Re-run `--analyze-from-files` to re-analyze TQL files
3. Verify TQL files use proper FLOW syntax:
   ```sql
   CREATE FLOW <flow_name>;
     CREATE SOURCE ...
     CREATE CQ ...
   END FLOW <flow_name>;
   ```

## Key Features and Recent Fixes

### Flow-Scoped Component Removal
The tool correctly handles components inside FLOW blocks by using `ALTER FLOW` commands:
```sql
ALTER APPLICATION <name>;
ALTER FLOW <flow_name>;
DROP <component_type> <name>;
END FLOW <flow_name>;
ALTER APPLICATION <name> RECOMPILE;
```

This prevents errors where components in one flow would affect components in other flows.

### CQ Detection with UDFs
The tool detects CQs that call custom UDFs using the pattern:
```
com.striim.util.AdvFormat.LowercaseTableName(...)
```

CQs are removed and restored along with OPs and UDFs because they depend on the UDF libraries.

### Library-Based Unload/Load
Instead of tracking individual component instances, the tool:
- Unloads ALL libraries from `LIST LIBRARIES` (not just detected components)
- Auto-detects whether each library is an OP or UDF by trying both LOAD/UNLOAD commands
- Handles multiple files in a single `--load-components` call

### HALTED/TERMINATED App Support
Apps in HALTED or TERMINATED states are now:
- Properly undeployed before component removal
- Restored to DEPLOYED state (not back to HALTED/TERMINATED)

### Transitional State Detection
The tool detects and warns about apps in transitional states (STARTING, DEPLOYING, etc.) that cannot be safely processed.

## Safety Features

- **Dry-run mode** - Test without making changes
- **State persistence** - Resume if interrupted
- **Backup on reset** - State file backed up before deletion
- **Confirmation prompts** - Interactive wizard asks for confirmation on destructive operations
- **Flow-aware removal** - Correctly scopes DROP commands to the right FLOW
- **Auto-detection** - Automatically determines OP vs UDF when loading/unloading

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

