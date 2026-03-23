# Striim Upgrade Manager - API Command Examples

This document shows **exactly** what TQL commands the Upgrade Manager executes via the Striim API for sample applications.

## Sample Applications

We have three example TQL files in the `examples/` directory:

1. **`sample_app_with_op.tql`** - Application using StriimWatcher OP
2. **`sample_app_with_processor.tql`** - Application using EventChanger OP
3. **`sample_app_with_udf.tql`** - Application using custom UDFs

---

## Example 1: Application with StriimWatcher OP

### Input TQL File: `sample_app_with_op.tql`

<augment_code_snippet path="examples/sample_app_with_op.tql" mode="EXCERPT">
```sql
CREATE OR REPLACE APPLICATION MonitoringApp;

CREATE OR REPLACE SOURCE MyStriimWatcher USING Global.StriimWatcher VERSION '5.2.5' (
  RepeatInSeconds: '300',
  IncludeNodeMonitor: true,
  ...
)
OUTPUT TO MonitoringStream;
```
</augment_code_snippet>

### What the Tool Detects

```json
{
  "type": "OP",
  "name": "admin.MyStriimWatcher",
  "namespace": "admin",
  "app_name": "MonitoringApp",
  "create_statement": "CREATE OR REPLACE SOURCE MyStriimWatcher USING Global.StriimWatcher VERSION '5.2.5' (\n  RepeatInSeconds: '300',\n  IncludeNodeMonitor: true,\n  IncludeNodeCluster: true,\n  IncludeAppDetail: true,\n  IncludeAppStatusDetail: true,\n  IncludeLee: true,\n  IncludeTableComparisonDetail: true\n)\nOUTPUT TO MonitoringStream;"
}
```

### API Commands Executed

#### Phase 1: Remove from Application (Pre-Upgrade)

```sql
-- Step 1: Undeploy the application
UNDEPLOY APPLICATION admin.MonitoringApp;

-- Step 2: Enter ALTER mode
ALTER APPLICATION admin.MonitoringApp;

-- Step 3: Drop the component (just DROP with qualified name)
DROP admin.MyStriimWatcher;

-- Step 4: Recompile the application
ALTER APPLICATION admin.MonitoringApp RECOMPILE;
```

**API Endpoint:** `POST /api/v2/tungsten`
**Request Body:**
```json
{
  "command": "UNDEPLOY APPLICATION admin.MonitoringApp;"
}
```

#### Phase 2: Unload from Striim (Pre-Upgrade)

```sql
-- Unload the StriimWatcher library
UNLOAD OPEN PROCESSOR 'UploadedFiles/StriimWatcher-5.2.5.jar';
```

**API Endpoint:** `POST /api/v2/tungsten`
**Request Body:**
```json
{
  "command": "UNLOAD OPEN PROCESSOR 'UploadedFiles/StriimWatcher-5.2.5.jar';"
}
```

#### Phase 3: Load New Version (Post-Upgrade)

```sql
-- Load the new version (after uploading via UI)
LOAD OPEN PROCESSOR 'UploadedFiles/StriimWatcher-5.4.0.jar';
```

**API Endpoint:** `POST /api/v2/tungsten`
**Request Body:**
```json
{
  "command": "LOAD OPEN PROCESSOR 'UploadedFiles/StriimWatcher-5.4.0.jar';"
}
```

#### Phase 4: Restore to Application (Post-Upgrade)

```sql
-- Step 1: Enter ALTER mode
ALTER APPLICATION admin.MonitoringApp;

-- Step 2: Recreate the component
CREATE OR REPLACE SOURCE MyStriimWatcher USING Global.StriimWatcher VERSION '5.2.5' (
  RepeatInSeconds: '300',
  IncludeNodeMonitor: true,
  IncludeNodeCluster: true,
  IncludeAppDetail: true,
  IncludeAppStatusDetail: true,
  IncludeLee: true,
  IncludeTableComparisonDetail: true
)
OUTPUT TO MonitoringStream;

-- Step 3: Recompile the application
ALTER APPLICATION admin.MonitoringApp RECOMPILE;
```

**API Endpoint:** `POST /api/v2/tungsten`
**Request Body:**
```json
{
  "command": "ALTER APPLICATION admin.MonitoringApp;"
}
```

---

## Example 2: Application with EventChanger OP

### Input TQL File: `sample_app_with_processor.tql`

<augment_code_snippet path="examples/sample_app_with_processor.tql" mode="EXCERPT">
```sql
CREATE OR REPLACE APPLICATION CDCPipeline;

CREATE OPEN PROCESSOR DataEnricher USING Global.EventChanger (
  IncludedColumns: 'id,name,email,created_at,updated_at',
  SendDDLEvents: true,
  MetadataColumnMap: 'OP_TYPE=OperationName,LOAD_TS=Timestamp,SRC_TABLE=TableName'
)
INPUT FROM RawCDCStream
OUTPUT TO FilteredCDCStream;
```
</augment_code_snippet>

### What the Tool Detects

```json
{
  "type": "UDF",
  "name": "admin.MY_HASH",
  "namespace": "admin",
  "app_name": "DataTransformApp",
  "create_statement": "CREATE FUNCTION MY_HASH ..."
}
```

**Note:** UDF detection requires the original `CREATE FUNCTION` statement in the TQL. If UDFs are loaded globally and not defined in the app TQL, they won't be detected by the app-level scan.

### API Commands Executed

#### Phase 1: Remove from Application

```sql
UNDEPLOY APPLICATION admin.DataTransformApp;
ALTER APPLICATION admin.DataTransformApp;
DROP FUNCTION admin.MY_HASH;
DROP FUNCTION admin.MY_UPPERCASE;
ALTER APPLICATION admin.DataTransformApp RECOMPILE;
```

#### Phase 2: Unload from Striim

```sql
UNLOAD UDF 'UploadedFiles/MyCustomFunctions.jar';
```

**API Endpoint:** `POST /api/v2/tungsten`
**Request Body:**
```json
{
  "command": "UNLOAD UDF 'UploadedFiles/MyCustomFunctions.jar';"
}
```

#### Phase 3: Load New Version

```sql
LOAD JAR 'UploadedFiles/MyCustomFunctions-5.4.0.jar';
```

**API Endpoint:** `POST /api/v2/tungsten`
**Request Body:**
```json
{
  "command": "LOAD JAR 'UploadedFiles/MyCustomFunctions-5.4.0.jar';"
}
```

#### Phase 4: Restore to Application

```sql
ALTER APPLICATION admin.DataTransformApp;
CREATE FUNCTION MY_HASH ...;
CREATE FUNCTION MY_UPPERCASE ...;
ALTER APPLICATION admin.DataTransformApp RECOMPILE;
```

---

## Complete Workflow Example

Here's a complete example showing all API calls for upgrading a system with all three sample applications:

### Step 1: Analyze (Discovery Phase)

```bash
python3 striim_upgrade_manager.py --analyze
```

**API Calls Made:**
1. `POST /security/authenticate` - Get auth token
2. `POST /api/v2/tungsten` - Execute `LIST APPLICATIONS;`
3. `POST /api/v2/tungsten` - Execute `EXPORT APPLICATION ALL WITH PASSPHRASE '...';`
4. Parse exported TQL files to find OPs/UDFs

**Output:**
```
=== Analysis Results ===
Found 3 applications with components:

admin.MonitoringApp:
  - OP: admin.MyStriimWatcher

admin.CDCPipeline:
  - OP: admin.DataEnricher

admin.DataTransformApp:
  - UDF: admin.MY_HASH
  - UDF: admin.MY_UPPERCASE

Total: 2 OPs, 2 UDFs across 3 applications
```

### Step 2: Remove Components from Applications

```bash
python3 striim_upgrade_manager.py --remove-from-apps
```

**API Calls Made (in sequence):**

```sql
-- For admin.MonitoringApp
UNDEPLOY APPLICATION admin.MonitoringApp;
ALTER APPLICATION admin.MonitoringApp;
DROP admin.MyStriimWatcher;
ALTER APPLICATION admin.MonitoringApp RECOMPILE;

-- For admin.CDCPipeline
UNDEPLOY APPLICATION admin.CDCPipeline;
ALTER APPLICATION admin.CDCPipeline;
DROP admin.DataEnricher;
ALTER APPLICATION admin.CDCPipeline RECOMPILE;

-- For admin.DataTransformApp
UNDEPLOY APPLICATION admin.DataTransformApp;
ALTER APPLICATION admin.DataTransformApp;
DROP admin.MY_HASH;
DROP admin.MY_UPPERCASE;
ALTER APPLICATION admin.DataTransformApp RECOMPILE;
```

### Step 3: Unload Components from Striim

```bash
python3 striim_upgrade_manager.py --unload-components
```

**API Calls Made:**

```sql
UNLOAD OPEN PROCESSOR 'UploadedFiles/StriimWatcher-5.2.5.jar';
UNLOAD OPEN PROCESSOR 'UploadedFiles/EventChanger.jar';
UNLOAD UDF 'UploadedFiles/MyCustomFunctions.jar';
```

### Step 4: Upgrade Striim (Manual)

1. Stop Striim
2. Upgrade to new version
3. Start Striim
4. Upload new component JARs via UI

### Step 5: Load New Components

```bash
python3 striim_upgrade_manager.py --load-components --component-path UploadedFiles/StriimWatcher-5.4.0.jar
python3 striim_upgrade_manager.py --load-components --component-path UploadedFiles/EventChanger-5.4.0.jar
python3 striim_upgrade_manager.py --load-components --component-path UploadedFiles/MyCustomFunctions-5.4.0.jar
```

**API Calls Made:**

```sql
LOAD OPEN PROCESSOR 'UploadedFiles/StriimWatcher-5.4.0.jar';
LOAD OPEN PROCESSOR 'UploadedFiles/EventChanger-5.4.0.jar';
LOAD JAR 'UploadedFiles/MyCustomFunctions-5.4.0.jar';
```

### Step 6: Restore Components to Applications

```bash
python3 striim_upgrade_manager.py --restore-to-apps
```

**API Calls Made (in sequence):**

```sql
-- For admin.MonitoringApp
ALTER APPLICATION admin.MonitoringApp;
CREATE OR REPLACE SOURCE MyStriimWatcher USING Global.StriimWatcher VERSION '5.2.5' (...);
ALTER APPLICATION admin.MonitoringApp RECOMPILE;

-- For admin.CDCPipeline
ALTER APPLICATION admin.CDCPipeline;
CREATE OPEN PROCESSOR DataEnricher USING Global.EventChanger (...);
ALTER APPLICATION admin.CDCPipeline RECOMPILE;

-- For admin.DataTransformApp
ALTER APPLICATION admin.DataTransformApp;
CREATE FUNCTION MY_HASH ...;
CREATE FUNCTION MY_UPPERCASE ...;
ALTER APPLICATION admin.DataTransformApp RECOMPILE;
```

---

## Dry-Run Example

To see what would happen without executing:

```bash
python3 striim_upgrade_manager.py --dry-run --remove-from-apps
```

**Output:**
```
=== Removing Components from Applications ===

Processing admin.MonitoringApp...
  [DRY-RUN] Would undeploy admin.MonitoringApp
  [DRY-RUN] Would remove OP admin.MyStriimWatcher

Processing admin.CDCPipeline...
  [DRY-RUN] Would undeploy admin.CDCPipeline
  [DRY-RUN] Would remove OP admin.DataEnricher

Processing admin.DataTransformApp...
  [DRY-RUN] Would undeploy admin.DataTransformApp
  [DRY-RUN] Would remove UDF admin.MY_HASH
  [DRY-RUN] Would remove UDF admin.MY_UPPERCASE

[OK] All components removed from applications (DRY-RUN)
```

---

## API Request/Response Examples

### Authentication

**Request:**
```http
POST /security/authenticate HTTP/1.1
Host: localhost:9081
Content-Type: application/json

{
  "username": "admin",
  "password": "admin"
}
```

**Response:**
```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expiresAt": 1711234567890
}
```

### Execute TQL Command

**Request:**
```http
POST /api/v2/tungsten HTTP/1.1
Host: localhost:9081
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
Content-Type: application/json

{
  "command": "UNDEPLOY APPLICATION admin.MonitoringApp;"
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Application undeployed successfully"
}
```

---

## State File Example

After running `--analyze`, the `upgrade_state.json` file contains:

```json
{
  "phase": "analyzed",
  "timestamp": "2024-03-22T19:45:00.123456",
  "apps_with_components": {
    "admin.MonitoringApp": [
      {
        "type": "OP",
        "name": "admin.MyStriimWatcher",
        "create_statement": "CREATE OR REPLACE SOURCE MyStriimWatcher USING Global.StriimWatcher VERSION '5.2.5' (\n  RepeatInSeconds: '300',\n  IncludeNodeMonitor: true,\n  IncludeNodeCluster: true,\n  IncludeAppDetail: true,\n  IncludeAppStatusDetail: true,\n  IncludeLee: true,\n  IncludeTableComparisonDetail: true\n)\nOUTPUT TO MonitoringStream;",
        "namespace": "admin",
        "app_name": "MonitoringApp"
      }
    ],
    "admin.CDCPipeline": [
      {
        "type": "OP",
        "name": "admin.DataEnricher",
        "create_statement": "CREATE OPEN PROCESSOR DataEnricher USING Global.EventChanger (\n  IncludedColumns: 'id,name,email,created_at,updated_at',\n  SendDDLEvents: true,\n  MetadataColumnMap: 'OP_TYPE=OperationName,LOAD_TS=Timestamp,SRC_TABLE=TableName'\n)\nINPUT FROM RawCDCStream\nOUTPUT TO FilteredCDCStream;",
        "namespace": "admin",
        "app_name": "CDCPipeline"
      }
    ],
    "admin.DataTransformApp": [
      {
        "type": "UDF",
        "name": "admin.MY_HASH",
        "create_statement": "CREATE FUNCTION MY_HASH ...",
        "namespace": "admin",
        "app_name": "DataTransformApp"
      },
      {
        "type": "UDF",
        "name": "admin.MY_UPPERCASE",
        "create_statement": "CREATE FUNCTION MY_UPPERCASE ...",
        "namespace": "admin",
        "app_name": "DataTransformApp"
      }
    ]
  },
  "removed_components": {},
  "unloaded_components": [],
  "loaded_components": [],
  "restored_apps": []
}
```

---

## Next Steps

1. **Review the sample TQL files** in `examples/` directory
2. **Run analyze against your environment:**
   ```bash
   python3 striim_upgrade_manager.py --analyze
   ```
3. **Check the state file:**
   ```bash
   python3 striim_upgrade_manager.py --status
   ```
4. **Test with dry-run:**
   ```bash
   python3 striim_upgrade_manager.py --dry-run --prepare-for-upgrade
   ```


