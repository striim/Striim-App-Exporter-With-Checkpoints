#!/usr/bin/env python3
"""
Configuration file for Striim Bulk Checkpoint Position Updater

This file contains all the configuration settings for connecting to Striim
and processing applications. Modify these values according to your environment.
"""

# Striim Export Server Configuration
STRIIM_CONFIG_EXPORT = {
    # Striim server URL (include protocol and port)
    'url': 'http://localhost:9080',

    # Authentication credentials
    'username': 'admin',
    'password': 'admin',

    # Export passphrase for zip file encryption
    'passphrase': 'striim123',
}

# Striim Import Server Configuration
STRIIM_CONFIG_IMPORT = {
    # Striim server URL (include protocol and port)
    'url': 'http://localhost:9080',

    # Authentication credentials
    'username': 'admin',
    'password': 'admin',

    # Import passphrase for TQL file decryption
    'passphrase': 'striim123',
}

# Processing Configuration
PROCESSING_CONFIG = {
    # Default directory for exported applications
    'stage_directory': 'stage',

    # Default directory for importing applications
    'import_directory': 'import',

    # Whether to overwrite existing files by default
    'overwrite_existing': True,

    # Supported reader types for checkpoint processing
    'supported_readers': ['Global.MysqlReader', 'Global.MSSqlReader', 'Global.MSJet', 'Global.MongoDBReader', 'Global.OracleReader', 'Global.OJet'],
}

# Advanced Configuration (usually don't need to change)
ADVANCED_CONFIG = {
    # API endpoint paths
    'auth_endpoint': '/security/authenticate',
    'api_endpoint': '/api/v2/tungsten',
    
    # Request timeouts (in seconds)
    'request_timeout': 30,
    'auth_timeout': 10,
    
    # Export file naming
    'export_zip_name': 'all_applications.zip',
}

# Environment-specific configurations
# You can add different configurations for different environments
ENVIRONMENTS = {
    'development': {
        'url': 'http://localhost:9080',
        'username': 'admin',
        'password': 'admin',
        'passphrase': 'dev123',
    },
    
    'staging': {
        'url': 'http://staging-striim:9080',
        'username': 'striim_user',
        'password': 'staging_password',
        'passphrase': 'staging456',
    },
    
    'production': {
        'url': 'https://prod-striim:9081',
        'username': 'prod_user',
        'password': 'prod_password',
        'passphrase': 'prod789secure',
    },
}

def get_config(environment='default'):
    """
    Get export configuration for a specific environment.

    Args:
        environment (str): Environment name ('default', 'development', 'staging', 'production')

    Returns:
        dict: Export configuration dictionary
    """
    if environment == 'default':
        return STRIIM_CONFIG_EXPORT
    elif environment in ENVIRONMENTS:
        return ENVIRONMENTS[environment]
    else:
        raise ValueError(f"Unknown environment: {environment}. Available: {list(ENVIRONMENTS.keys())}")

def get_import_config(environment='default'):
    """
    Get import configuration for a specific environment.

    Args:
        environment (str): Environment name ('default', 'development', 'staging', 'production')

    Returns:
        dict: Import configuration dictionary
    """
    if environment == 'default':
        return STRIIM_CONFIG_IMPORT
    elif environment in ENVIRONMENTS:
        # For environments, use the same config as export (can be customized if needed)
        return ENVIRONMENTS[environment]
    else:
        raise ValueError(f"Unknown environment: {environment}. Available: {list(ENVIRONMENTS.keys())}")

def get_processing_config():
    """Get processing configuration."""
    return PROCESSING_CONFIG

def get_advanced_config():
    """Get advanced configuration."""
    return ADVANCED_CONFIG

# Example usage:
if __name__ == "__main__":
    print("=== Default Configuration ===")
    config = get_config()
    for key, value in config.items():
        if key == 'password':
            print(f"{key}: {'*' * len(value)}")  # Hide password
        else:
            print(f"{key}: {value}")
    
    print("\n=== Available Environments ===")
    for env in ENVIRONMENTS.keys():
        print(f"- {env}")
    
    print("\n=== Processing Configuration ===")
    proc_config = get_processing_config()
    for key, value in proc_config.items():
        print(f"{key}: {value}")
