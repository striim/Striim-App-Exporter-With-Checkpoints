#!/bin/bash
#
# Striim Upgrade Wizard - Interactive Interface
#
# This script provides an interactive menu-driven interface for the
# Striim Upgrade Manager, guiding users through the upgrade process.
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/striim_upgrade_manager.py"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_header() {
    echo -e "${BLUE}============================================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}============================================================${NC}"
}

print_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

show_menu() {
    clear
    print_header "Striim Upgrade Manager - Interactive Wizard"
    echo ""
    echo "PRE-UPGRADE STEPS:"
    echo "  1) Analyze applications (find OPs/UDFs)"
    echo "  2) Remove OPs/UDFs from applications"
    echo "  3) Unload OPs/UDFs from Striim"
    echo "  4) Run all pre-upgrade steps (1-3)"
    echo ""
    echo "POST-UPGRADE STEPS:"
    echo "  5) Load new OP/UDF components"
    echo "  6) Restore OPs/UDFs to applications"
    echo ""
    echo "UTILITIES:"
    echo "  7) Check upgrade status"
    echo "  8) Dry-run mode (test without changes)"
    echo "  9) Reset upgrade state"
    echo ""
    echo "  0) Exit"
    echo ""
    echo -n "Select an option: "
}

run_python_command() {
    local cmd="$1"
    local description="$2"

    print_info "$description"
    echo ""
    echo "Command: python3 $PYTHON_SCRIPT $cmd"
    echo ""
    read -p "Press Enter to continue or Ctrl+C to cancel..."

    python3 "$PYTHON_SCRIPT" $cmd
    local exit_code=$?

    echo ""
    if [ $exit_code -eq 0 ]; then
        print_success "Command completed successfully"
    else
        print_error "Command failed with exit code $exit_code"
    fi

    echo ""
    read -p "Press Enter to continue..."
}

analyze_apps() {
    # Check if export file already exists
    local export_file="$SCRIPT_DIR/upgrade_backup/all_applications.zip"

    if [ -f "$export_file" ]; then
        echo ""
        print_info "Found existing export file: $export_file"
        echo ""
        echo "Choose analysis mode:"
        echo "  1) Re-analyze from existing files (fast, no re-export)"
        echo "  2) Full analysis (re-export and analyze)"
        echo "  0) Cancel"
        echo ""
        read -p "Select: " choice

        case $choice in
            1)
                run_python_command "--analyze-from-files" "Re-analyzing from existing TQL files..."
                ;;
            2)
                run_python_command "--analyze" "Running full analysis (export and analyze)..."
                ;;
            0)
                print_info "Cancelled"
                read -p "Press Enter to continue..."
                return
                ;;
            *)
                print_error "Invalid option"
                read -p "Press Enter to continue..."
                return
                ;;
        esac
    else
        # No existing export, run full analysis
        run_python_command "--analyze" "Analyzing applications for OPs and UDFs..."
    fi
}

remove_from_apps() {
    print_warning "This will UNDEPLOY applications and remove OPs/UDFs!"
    read -p "Are you sure? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        print_info "Cancelled"
        read -p "Press Enter to continue..."
        return
    fi
    run_python_command "--remove-from-apps" "Removing OPs/UDFs from applications..."
}

unload_components() {
    run_python_command "--unload-components" "Unloading OPs/UDFs from Striim..."
}

prepare_for_upgrade() {
    print_warning "This will run ALL pre-upgrade steps!"
    read -p "Are you sure? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        print_info "Cancelled"
        read -p "Press Enter to continue..."
        return
    fi
    run_python_command "--prepare-for-upgrade" "Running all pre-upgrade steps..."
}

load_components() {
    echo ""
    print_info "Enter the path to the component file (e.g., UploadedFiles/MyOP.scm)"
    read -p "Component path: " comp_path

    if [ -z "$comp_path" ]; then
        print_error "No path provided"
        read -p "Press Enter to continue..."
        return
    fi

    run_python_command "--load-components --component-path $comp_path" "Loading component..."
}

restore_to_apps() {
    print_warning "This will restore OPs/UDFs to applications!"
    read -p "Are you sure? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        print_info "Cancelled"
        read -p "Press Enter to continue..."
        return
    fi
    run_python_command "--restore-to-apps" "Restoring OPs/UDFs to applications..."
}

check_status() {
    run_python_command "--status" "Checking upgrade status..."
}

dry_run_menu() {
    clear
    print_header "Dry-Run Mode"
    echo ""
    echo "Select action to test:"
    echo "  1) Analyze (full export)"
    echo "  2) Analyze from existing files"
    echo "  3) Remove from apps"
    echo "  4) Unload components"
    echo "  0) Back"
    echo ""
    read -p "Select: " choice

    case $choice in
        1) run_python_command "--dry-run --analyze" "Dry-run: Analyze (full)" ;;
        2) run_python_command "--dry-run --analyze-from-files" "Dry-run: Analyze from files" ;;
        3) run_python_command "--dry-run --remove-from-apps" "Dry-run: Remove from apps" ;;
        4) run_python_command "--dry-run --unload-components" "Dry-run: Unload components" ;;
        0) return ;;
        *) print_error "Invalid option"; read -p "Press Enter..."; ;;
    esac
}

reset_state() {
    print_warning "This will reset the upgrade state!"
    print_warning "A backup will be created before resetting."
    read -p "Are you sure? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        print_info "Cancelled"
        read -p "Press Enter to continue..."
        return
    fi
    run_python_command "--reset-state" "Resetting upgrade state..."
}

# Main loop
main() {
    # Check if Python script exists
    if [ ! -f "$PYTHON_SCRIPT" ]; then
        print_error "Python script not found: $PYTHON_SCRIPT"
        exit 1
    fi

    while true; do
        show_menu
        read choice

        case $choice in
            1) analyze_apps ;;
            2) remove_from_apps ;;
            3) unload_components ;;
            4) prepare_for_upgrade ;;
            5) load_components ;;
            6) restore_to_apps ;;
            7) check_status ;;
            8) dry_run_menu ;;
            9) reset_state ;;
            0)
                echo ""
                print_info "Exiting..."
                exit 0
                ;;
            *)
                print_error "Invalid option"
                read -p "Press Enter to continue..."
                ;;
        esac
    done
}

# Run main
main

