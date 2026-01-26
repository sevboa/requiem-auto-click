from __future__ import annotations

ORG_NAME = "RequiemTools"
APP_NAME = "RequiemAutoClick"

# Global settings keys are stored by sa-ui-operations-base under:
#   global/settings/<SETTING_KEY>
LAUNCHER_COMMAND_SETTING_KEY = "launcher_command"
REFRESH_INTERVAL_SECONDS_SETTING_KEY = "refresh_interval_seconds"

# Plugin (tab) settings keys are stored under:
#   tabs/<tab_id>/settings/<SETTING_KEY>
LOGIN_ENTER_DELAY_SECONDS_SETTING_KEY = "login_enter_delay_seconds"

# Autologin (tab) settings:
AUTOLOGIN_WAIT_HWND_TIMEOUT_SECONDS_SETTING_KEY = "autologin_wait_hwnd_timeout_seconds"
AUTOLOGIN_LOGIN_TIMEOUT_SECONDS_SETTING_KEY = "autologin_login_timeout_seconds"
AUTOLOGIN_SELECT_SERVER_TIMEOUT_SECONDS_SETTING_KEY = "autologin_select_server_timeout_seconds"
AUTOLOGIN_ENTER_CHAR_TIMEOUT_SECONDS_SETTING_KEY = "autologin_enter_char_timeout_seconds"
AUTOLOGIN_PIN_BLOCK_TIMEOUT_SECONDS_SETTING_KEY = "autologin_pin_block_timeout_seconds"
AUTOLOGIN_PIN_DIGIT_TIMEOUT_SECONDS_SETTING_KEY = "autologin_pin_digit_timeout_seconds"
AUTOLOGIN_PIN_DELAY_MS_SETTING_KEY = "autologin_pin_delay_ms"

# Sequential start policy (tab) settings:
# 0 = skip, 1 = retry, 2 = stop
AUTOLOGIN_ERROR_POLICY_SETTING_KEY = "autologin_error_policy"
AUTOLOGIN_RETRY_ATTEMPTS_SETTING_KEY = "autologin_retry_attempts"

# Shared (global) state keys:
# Stored under QSettings key: global/<LOCAL_KEY>
LAUNCHER_ROWS_JSON_GLOBAL_KEY = "launcher/rows_json"
LAUNCHER_WINDOWS_JSON_GLOBAL_KEY = "launcher/windows_json"