# Drop Types Functionality

The `--droptypes` flag has been added to the `striim_export_all_with_checkpoint.py` script to replicate the functionality of the bash script for dropping types based on a naming convention. It supports both **explicit** and **auto-detection** modes.

## Usage

### Explicit Mode (Manual Specification)
```bash
# Drop types with prefix "admin.MySource_" before exporting
python3 striim_export_all_with_checkpoint.py --droptypes admin MySource

# Drop types and export to a specific directory
python3 striim_export_all_with_checkpoint.py --droptypes admin MySource --stage-dir /path/to/export

# Drop types with environment-specific configuration
python3 striim_export_all_with_checkpoint.py --droptypes admin MySource --environment production
```

### Auto-Detection Mode (Automatic)
```bash
# Auto-detect source components from applications and drop their types
python3 striim_export_all_with_checkpoint.py --droptypes

# Alternative syntax for auto-detection
python3 striim_export_all_with_checkpoint.py --droptypes-auto

# Auto-detect with environment configuration
python3 striim_export_all_with_checkpoint.py --droptypes --environment production
```

## How it works

### Explicit Mode
1. **Prefix Construction**: The script constructs a prefix from the provided namespace and source component name:
   ```
   prefix = "{namespace}.{source_component_name}_"
   ```

2. **Type Listing**: Uses the Striim API to execute `list types;` command

3. **Filtering**: Filters the returned types to only include those starting with the prefix

4. **Dropping**: For each matching type, executes `drop type {typename};`

### Auto-Detection Mode
1. **Application Discovery**: Gets the list of all applications from Striim

2. **Source Component Extraction**: For each application name, extracts the source component:
   - Splits application name by '.' (e.g., `admin.SQLCDCReader` → namespace: `admin`, component: `SQLCDCReader`)
   - Removes common suffixes like `_App`, `_Application`, `_Stream`
   - Collects unique source components

3. **Type Processing**: For each detected source component, follows the same process as explicit mode

4. **Batch Processing**: Processes all detected source components automatically

## Examples

### Explicit Mode Example
If you run:
```bash
python3 striim_export_all_with_checkpoint.py --droptypes admin MySource
```

The script will:
1. Look for types with prefix `admin.MySource_`
2. Find types like:
   - `admin.MySource_Table1`
   - `admin.MySource_Table2`
   - `admin.MySource_View1`
3. Drop each matching type using `drop type` commands

### Auto-Detection Mode Example
If you run:
```bash
python3 striim_export_all_with_checkpoint.py --droptypes
```

The script will:
1. Get applications: `admin.SQLCDCReader`, `admin.MySQLReader_Production`, `system.MonitorApp`
2. Extract source components:
   - `admin.SQLCDCReader` → `admin.SQLCDCReader_`
   - `admin.MySQLReader_Production` → `admin.MySQLReader_Production_`
   - `system.MonitorApp` → `system.MonitorApp_`
3. Drop types for each prefix automatically

## Integration with Export Process

The drop types functionality is integrated as an optional step in the export process:

1. **Step 1**: Authenticate with Striim
2. **Step 1.5** (if --droptypes specified): Drop matching types
3. **Step 2**: Get application list
4. **Step 3**: Export all applications
5. **Step 4**: Process applications for checkpoint updates

## Error Handling

- If some types fail to drop, the script will continue with the export process
- Each type drop operation is logged with success/failure status
- A summary is provided showing total types found and successfully dropped

## Comparison with Bash Script

The Python implementation provides the same core functionality as the bash script:

| Bash Script | Python Script |
|-------------|---------------|
| `./script.sh namespace source_component` | `python3 striim_export_all_with_checkpoint.py --droptypes namespace source_component` |
| Uses `console.sh` | Uses Striim REST API |
| Standalone operation | Integrated with export process |
| Manual execution | Can be combined with export workflow |

## Benefits of Python Implementation

1. **Integration**: Combined with the export process in a single command
2. **API-based**: Uses REST API instead of console commands
3. **Error Handling**: Better error reporting and handling
4. **Flexibility**: Can be easily extended or modified
5. **Configuration**: Leverages existing environment configuration system
