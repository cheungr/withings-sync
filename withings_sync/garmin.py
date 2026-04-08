"""This module handles the Garmin connectivity."""

import io
import logging
import os

from .garmin_auth import (
    Client as AuthClient,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

log = logging.getLogger("garmin")

HOME = os.getenv("HOME", ".")
GARMIN_SESSION = os.path.abspath(
    os.path.expanduser(
        os.getenv("GARMIN_SESSION", os.path.join(HOME, ".garmin_session"))
    )
)


class GarminConnect:
    """Main GarminConnect class.

    Uses the native DI OAuth2 authentication engine to communicate
    with Garmin Connect.  Session tokens are persisted as JSON so
    that subsequent runs can skip the login flow.
    """

    def __init__(self, config_folder=None) -> None:
        self.client = AuthClient()
        self.config_folder = config_folder

        if config_folder:
            self.session_path = os.path.join(config_folder, ".garmin_session")
        else:
            self.session_path = GARMIN_SESSION

        # Log helpful message if using new config folder and file doesn't exist
        if config_folder and not os.path.exists(self.session_path):
            home = os.getenv("HOME", ".")
            legacy_path = os.path.abspath(
                os.path.expanduser(os.path.join(home, ".garmin_session"))
            )
            if os.path.exists(legacy_path):
                log.info("Using new config folder: %s", self.session_path)
                log.info(
                    "Legacy session found at %s — note: old garth-format "
                    "sessions are NOT compatible. You will need to re-authenticate.",
                    legacy_path,
                )

    def login(self, email=None, password=None):
        """Login to Garmin Connect via DI OAuth2 with session persistence.

        Authentication flow:
        1. Try to load a previously saved token file (JSON).
        2. If no valid token exists, authenticate with email/password.
        3. If MFA is enabled on the account, credential-based login
           may not complete — set GARMIN_USERNAME / GARMIN_PASSWORD
           environment variables and ensure the token file is populated
           from a successful prior login.
        """
        log.debug("Attempting Garmin login via DI OAuth2")

        if not self.session_path:
            raise GarminConnectConnectionError(
                "Garmin session path is not configured. "
                "Set the GARMIN_SESSION environment variable to a writable file path."
            )

        # Step 1: Try loading an existing token file
        if os.path.exists(self.session_path):
            try:
                log.debug("Loading existing Garmin token file: %s", self.session_path)
                self.client.load(self.session_path)
                if self.client.is_authenticated:
                    log.info("Garmin session restored from token file")
                    return
                else:
                    log.warning(
                        "Token file exists but contains no valid tokens — "
                        "will re-authenticate with credentials"
                    )
            except Exception as ex:
                log.warning("Failed to load Garmin token file: %s", ex)

        # Step 2: Authenticate with credentials
        if not email or not password:
            raise GarminConnectAuthenticationError(
                "No valid Garmin session found and no credentials provided.\n"
                "Please provide credentials via:\n"
                "  • --garmin-username / --garmin-password CLI flags, or\n"
                "  • GARMIN_USERNAME / GARMIN_PASSWORD environment variables, or\n"
                "  • A .env file in the working directory.\n\n"
                "If your account has MFA enabled, you may need to:\n"
                "  1. Run withings-sync interactively once to complete MFA.\n"
                "  2. The DI OAuth token file will be saved automatically.\n"
                "  3. Subsequent runs will use the saved token file.\n\n"
                f"Token file location: {self.session_path}"
            )

        # Check write permissions BEFORE attempting authentication
        session_dir = os.path.dirname(self.session_path)
        if session_dir and not os.access(session_dir, os.W_OK):
            log.warning("Cannot write to session directory: %s", session_dir)

        try:
            log.info("Authenticating with Garmin Connect via DI OAuth2")
            self.client.login(email, password)
            log.info("Garmin DI OAuth2 authentication successful")

        except GarminConnectAuthenticationError:
            # Re-raise auth errors directly — they have good messages already
            raise
        except GarminConnectTooManyRequestsError:
            raise
        except Exception as ex:
            raise GarminConnectConnectionError(
                f"Garmin authentication failed: {ex}\n\n"
                "Possible causes:\n"
                "  • Incorrect email or password\n"
                "  • MFA is enabled (credential login requires interactive MFA completion)\n"
                "  • Garmin SSO service is temporarily unavailable\n"
                "  • Cloudflare is blocking the connection (try installing curl-cffi)\n\n"
                "To install curl-cffi for better Cloudflare bypass:\n"
                "  pip install curl-cffi ua-generator"
            ) from ex

        # Step 3: Persist tokens for future runs
        try:
            if session_dir:
                os.makedirs(session_dir, exist_ok=True)

            self.client.dump(self.session_path)
            log.info("Garmin DI OAuth tokens saved to %s", self.session_path)

        except Exception as ex:
            log.warning(
                "Authentication succeeded but token file could not be saved: %s. "
                "Next run will require re-authentication. "
                "Check that %s is writable.",
                ex,
                self.session_path,
            )

    def upload_file(self, ffile):
        """Upload a FIT file to Garmin Connect."""
        fit_file = io.BytesIO(ffile.getvalue())
        fit_file.name = "withings.fit"
        self.client.post(
            "connectapi",
            "/upload-service/upload",
            files={"file": ("withings.fit", fit_file)},
        )
        return True
