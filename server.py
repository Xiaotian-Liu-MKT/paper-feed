import http.server
import socketserver
import json
import os
import sys
from functools import partial

# 导入 RSS 抓取逻辑
# 确保 get_RSS.py 在同一目录下
try:
    from get_RSS import run_rss_flow, get_config
except ImportError:
    print("Error: Could not import run_rss_flow from get_RSS.py")
    sys.exit(1)

PORT = 8000
WEB_DIR = "web"
CONFIG_FILE = "config.json"
INTERACTIONS_FILE = os.path.join(WEB_DIR, "interactions.json")

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # 设置静态文件根目录为 web/
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_GET(self):
        # 解析路径，忽略 query parameters
        path = self.path.split('?')[0]

        # 添加一个 API 来获取当前配置（用于回显到前端）
        if path == '/api/config':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            config = get_config()
            # 处于安全考虑，返回时可以脱敏，或者因为是本地运行，直接返回方便编辑
            # 这里直接返回
            self.wfile.write(json.dumps(config).encode('utf-8'))
            return

        if path == '/api/interactions':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            if os.path.exists(INTERACTIONS_FILE):
                try:
                    with open(INTERACTIONS_FILE, 'r', encoding='utf-8') as f:
                        self.wfile.write(f.read().encode('utf-8'))
                except:
                    self.wfile.write(b'{"favorites": [], "hidden": []}')
            else:
                self.wfile.write(b'{"favorites": [], "hidden": []}')
            return

        return super().do_GET()

    def do_POST(self):
        if self.path == '/api/interactions':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                req_data = json.loads(post_data.decode('utf-8'))
                
                # Load existing
                data = {"favorites": [], "hidden": []}
                if os.path.exists(INTERACTIONS_FILE):
                    try:
                        with open(INTERACTIONS_FILE, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                    except:
                        pass
                
                action = req_data.get("action")
                item_id = req_data.get("id") # Using link as ID
                
                if action and item_id:
                    if action == "like":
                        if item_id not in data["favorites"]:
                            data["favorites"].append(item_id)
                        if item_id in data["hidden"]:
                            data["hidden"].remove(item_id)
                    elif action == "unlike":
                        if item_id in data["favorites"]:
                            data["favorites"].remove(item_id)
                    elif action == "hide":
                        if item_id not in data["hidden"]:
                            data["hidden"].append(item_id)
                        if item_id in data["favorites"]:
                            data["favorites"].remove(item_id)
                    elif action == "unhide":
                        if item_id in data["hidden"]:
                            data["hidden"].remove(item_id)
                
                with open(INTERACTIONS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(data).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        if self.path == '/api/save_config':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                new_config = json.loads(post_data.decode('utf-8'))
                # 读取旧配置以合并（如果有其他字段）
                current_config = {}
                if os.path.exists(CONFIG_FILE):
                    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                        current_config = json.load(f)
                
                current_config.update(new_config)
                
                # 写入文件
                # 这里的 CONFIG_FILE 是在根目录下，不是 web/ 下，更安全
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(current_config, f, ensure_ascii=False, indent=2)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "message": "Config saved"}).encode('utf-8'))
                
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        if self.path == '/api/fetch':
            print("Received fetch request...")
            try:
                # 运行爬虫和翻译
                run_rss_flow()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "message": "Fetch completed"}).encode('utf-8'))
            except Exception as e:
                print(f"Fetch error: {e}")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        return super().do_POST()

def run_server():
    # 允许地址重用，防止重启时端口被占
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('', PORT), CustomHandler) as httpd:
        print(f"Server started at http://localhost:{PORT}")
        print("Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server...")
            httpd.shutdown()

if __name__ == "__main__":
    run_server()
