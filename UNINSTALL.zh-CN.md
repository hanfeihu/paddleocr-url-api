# 卸载说明

本文档说明如何卸载 OCR URL API 服务。

---

## macOS（离线安装包，LaunchDaemon）

安装形态
- 安装目录：`/usr/local/paddleocr-url-api-offline`
- 服务配置：`/Library/LaunchDaemons/com.paddleocr.urlapi.offline.plist`
- 日志：`/var/log/paddleocr-url-api.offline.out.log`、`/var/log/paddleocr-url-api.offline.err.log`

卸载步骤
1) 停止并卸载服务

```bash
sudo launchctl unload /Library/LaunchDaemons/com.paddleocr.urlapi.offline.plist
```

2) 删除服务配置、程序文件与日志

```bash
sudo rm -f /Library/LaunchDaemons/com.paddleocr.urlapi.offline.plist
sudo rm -rf /usr/local/paddleocr-url-api-offline
sudo rm -f /var/log/paddleocr-url-api.offline.out.log /var/log/paddleocr-url-api.offline.err.log
```

3) 验证 8000 端口已关闭

```bash
lsof -iTCP:8000 -sTCP:LISTEN -n -P
```

---

## macOS（用户 LaunchAgent，旧版残留）

如果你以前装过用户级 LaunchAgent（旧的开发机方式），再执行：

```bash
launchctl unload "$HOME/Library/LaunchAgents/com.a1.paddleocr-url-api.plist" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/com.a1.paddleocr-url-api.plist"
rm -f "$HOME/Library/Logs/paddleocr-url-api.out.log" "$HOME/Library/Logs/paddleocr-url-api.err.log"
```

---

## Windows（安装器 + WinSW 服务）

安装形态
- 主要分发形式为安装器 `ocr-url-api-setup-1.0.9.exe`
- 安装器会把程序复制到 `Program Files\OCR URL API`
- 安装后的目录仍包含 `ocr-url-api.exe`、`models\`、WinSW 服务文件和辅助脚本

卸载步骤
1) 打开 Windows 的 **应用和功能** 或 **已安装的应用**
2) 卸载 **OCR URL API**

手动兜底方式
1) 以管理员身份打开 CMD
2) `cd` 到已安装目录
3) 执行

```bat
uninstall-service.bat
```

验证 8000 端口

```bat
netstat -ano | findstr :8000
```
