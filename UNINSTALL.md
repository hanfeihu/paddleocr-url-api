# Uninstall

This document describes how to uninstall the OCR URL API service.

---

## macOS (Offline Installer, LaunchDaemon)

Install type
- Installed to: `/usr/local/paddleocr-url-api-offline`
- Service: `/Library/LaunchDaemons/com.paddleocr.urlapi.offline.plist`
- Logs: `/var/log/paddleocr-url-api.offline.out.log`, `/var/log/paddleocr-url-api.offline.err.log`

Uninstall steps
1) Stop and unload the daemon

```bash
sudo launchctl unload /Library/LaunchDaemons/com.paddleocr.urlapi.offline.plist
```

2) Remove the daemon plist, program files, and logs

```bash
sudo rm -f /Library/LaunchDaemons/com.paddleocr.urlapi.offline.plist
sudo rm -rf /usr/local/paddleocr-url-api-offline
sudo rm -f /var/log/paddleocr-url-api.offline.out.log /var/log/paddleocr-url-api.offline.err.log
```

3) Verify port 8000 is not listening

```bash
lsof -iTCP:8000 -sTCP:LISTEN -n -P
```

---

## macOS (User LaunchAgent, Legacy)

If you previously installed the user-level LaunchAgent (legacy dev setup), remove it too:

```bash
launchctl unload "$HOME/Library/LaunchAgents/com.a1.paddleocr-url-api.plist" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/com.a1.paddleocr-url-api.plist"
rm -f "$HOME/Library/Logs/paddleocr-url-api.out.log" "$HOME/Library/Logs/paddleocr-url-api.err.log"
```

---

## Windows (Installer + WinSW Service)

Install type
- Primary distribution is the installer `ocr-url-api-setup-1.0.10.exe`
- The installer copies the application into `Program Files\OCR URL API`
- The installed payload contains `ocr-url-api.exe`, `models\`, WinSW service files, and helper scripts

Uninstall steps
1) Open **Apps & Features** or **Installed apps** in Windows
2) Uninstall **OCR URL API**

Manual fallback
1) Open **Command Prompt as Administrator**
2) `cd` into the installed application folder
3) Run

```bat
uninstall-service.bat
```

Verify port 8000

```bat
netstat -ano | findstr :8000
```
