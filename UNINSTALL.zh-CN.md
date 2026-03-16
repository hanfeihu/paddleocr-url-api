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

## Windows（WinSW 服务）

安装形态
- 解压后的目录包含：`ocr-url-api.exe`、`models\`、`install-service.bat`、`uninstall-service.bat` 等
- 服务由 WinSW 包装为 Windows Service

卸载步骤
1) 以管理员身份打开 CMD
2) `cd` 到解压目录
3) 执行

```bat
uninstall-service.bat
```

4) 删除整个解压目录

验证 8000 端口

```bat
netstat -ano | findstr :8000
```
