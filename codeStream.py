import os
import time
import sys
import socket
import difflib
import mimetypes
import re
from flask import Flask, render_template_string, jsonify, send_file
from zeroconf import ServiceInfo, Zeroconf

app = Flask(__name__)

# --- КОНФІГУРАЦІЯ ---
PORT = 80
ROOT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

# Визначаємо домен на основі назви файлу
raw_domain = ""
if getattr(sys, 'frozen', False):
    exe_name = os.path.basename(sys.executable)
    match = re.search(r'\((.*?)\)', exe_name)
    if match:
        raw_domain = match.group(1) # Беремо те, що в дужках
    else:
        raw_domain = os.path.splitext(exe_name)[0]
else:
    raw_domain = "code"

# Санітизація: замінюємо пробіли на дефіси, прибираємо все крім a-z, 0-9 і дефіса
DOMAIN = re.sub(r'[^a-zA-Z0-9\-]', '', raw_domain.replace(' ', '-')).lower()
if not DOMAIN:
    DOMAIN = "code"

# Фільтр сміттєвих файлів та папок
IGNORE_DIRS = {'.git', '.idea', '.vscode', '__pycache__', 'venv', 'env', 'node_modules', 'Logs', 'Library'}
IGNORE_EXTS = {'.meta', '.pyc', '.exe', '.dll', '.so', '.DS_Store', '.userprefs', '.pidb'}

file_baselines = {}
last_mtime = {}

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def get_files():
    valid_files = []
    for root, dirs, files in os.walk(ROOT_DIR):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith('.')]
        for f in files:
            if any(f.endswith(ext) for ext in IGNORE_EXTS) or f.startswith('.'):
                continue
            rel_path = os.path.relpath(os.path.join(root, f), ROOT_DIR)
            valid_files.append(rel_path.replace('\\', '/'))
    return sorted(valid_files)

def get_changed_lines(filename, current_content):
    now = time.time()
    if filename not in file_baselines or (now - file_baselines[filename][0] > 600):
        file_baselines[filename] = (now, current_content)
        return []

    old_content = file_baselines[filename][1].splitlines()
    new_content = current_content.splitlines()
    
    changed_indices = []
    diff = list(difflib.ndiff(old_content, new_content))
    
    current_line = 1
    for line in diff:
        if line.startswith('  '): 
            current_line += 1
        elif line.startswith('+ '): 
            changed_indices.append(current_line)
            current_line += 1
        elif line.startswith('- '): 
            continue
            
    return changed_indices

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Code Stream</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/themes/prism-tomorrow.min.css" rel="stylesheet" />
    <link href="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/plugins/line-highlight/prism-line-highlight.min.css" rel="stylesheet" />
    <link href="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/plugins/line-numbers/prism-line-numbers.min.css" rel="stylesheet" />
    <style>
        :root { --bg: #0f0f0f; --panel: #1a1a1a; --accent: #00ff41; --text: #e0e0e0; }
        body { margin: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: var(--bg); color: var(--text); display: flex; flex-direction: column; height: 100vh; }
        
        #header { background: var(--panel); padding: 10px 20px; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; }
        .links a { font-family: monospace; font-size: 1.1em; color: var(--accent); text-decoration: none; }
        .links a:hover { text-decoration: underline; }
        .links span { color: #888; margin-right: 5px; }
        
        #main { display: flex; flex: 1; overflow: hidden; }
        #sidebar { width: 250px; background: var(--panel); border-right: 1px solid #333; display: flex; flex-direction: column; flex-shrink: 0; }
        #sidebar h3 { font-size: 14px; text-transform: uppercase; color: #666; letter-spacing: 1px; margin: 15px 15px 10px 15px; flex-shrink: 0; }
        
        #file-list { flex: 1; overflow-y: auto; padding: 0 10px 15px 5px; }
        
        .tree-item { 
            display: flex; justify-content: space-between; align-items: center; 
            padding: 4px 8px; color: #bbb; text-decoration: none; border-radius: 4px; 
            margin-bottom: 2px; font-size: 14px; cursor: pointer; user-select: none;
            transition: background 0.1s;
        }
        .tree-item:hover { background: #2a2a2a; color: #fff; }
        .tree-item.active { background: #005a17; color: #fff; border-left: 3px solid var(--accent); }
        
        .tree-label { display: flex; align-items: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; }
        .tree-arrow { font-size: 10px; width: 16px; text-align: center; display: inline-block; color: #888; }
        .tree-icon { margin-right: 6px; font-size: 14px; }
        
        .dl-btn { 
            opacity: 0; text-decoration: none; font-size: 14px; background: #333; 
            border-radius: 4px; padding: 2px 6px; transition: 0.2s; 
        }
        .tree-item:hover .dl-btn { opacity: 1; }
        .dl-btn:hover { background: var(--accent); transform: scale(1.1); }

        #content-area { flex: 1; display: flex; flex-direction: column; background: #050505; position: relative; min-width: 0; }
        #toolbar { padding: 10px 20px; background: #111; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; min-height: 30px; }
        #current-filename { font-weight: bold; color: #aaa; }
        
        .btn { background: #222; color: #fff; border: 1px solid #444; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 13px; transition: 0.2s; }
        .btn:hover { background: #333; border-color: var(--accent); }
        .btn:active { background: var(--accent); color: #000; }

        #code-container { flex: 1; overflow: auto; padding: 0; }
        
        pre[class*="language-"] { 
            margin: 0 !important; border-radius: 0 !important; background: transparent !important; 
            font-size: 14px !important; white-space: pre-wrap !important; word-break: break-word !important;
            padding-left: 3.8em !important;
        }
        code[class*="language-"] { white-space: pre-wrap !important; word-break: break-word !important; }
        .line-highlight { background: rgba(0, 255, 65, 0.15) !important; border-left: 3px solid var(--accent); }
        
        #image-container { display: none; padding: 20px; text-align: center; overflow: auto; height: 100%; box-sizing: border-box;}
        #image-container img { max-width: 100%; max-height: 100%; object-fit: contain; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
    </style>
</head>
<body>

<div id="header">
    <div style="font-weight: bold; font-size: 1.2em;">👨‍💻 Code Stream</div>
    <div class="links">
        <span>Лінк:</span> 
        <a href="{{ domain_url }}">{{ domain_url }}</a> &nbsp;|&nbsp; <a href="{{ ip_url }}">{{ ip_url }}</a>
    </div>
</div>

<div id="main">
    <div id="sidebar">
        <h3>Файли проекту</h3>
        <div id="file-list"></div>
    </div>
    <div id="content-area">
        <div id="toolbar">
            <span id="current-filename">Оберіть файл...</span>
            <button id="copy-btn" class="btn" onclick="copyCode()" style="display:none;">📋 Копіювати код</button>
        </div>
        <div id="code-container">
            <pre id="pre-block" class="line-numbers"><code id="code-block" class="language-python"></code></pre>
        </div>
        <div id="image-container">
            <img id="image-viewer" src="" alt="Image Viewer">
        </div>
    </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/prism.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-python.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-csharp.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/plugins/line-highlight/prism-line-highlight.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/plugins/line-numbers/prism-line-numbers.min.js"></script>

<script>
    let currentFile = "";
    let lastContent = "";
    let fileList = [];
    let openFolders = new Set(['']);

    function getFileIcon(filename) {
        if (!filename.includes('.')) return '📁';
        const ext = filename.split('.').pop().toLowerCase();
        const icons = {
            'py': '🐍', 'js': '💛', 'ts': '💙', 'html': '🌐', 'css': '🎨',
            'json': '📦', 'md': '📝', 'txt': '📄', 'csv': '📊',
            'png': '🖼️', 'jpg': '🖼️', 'jpeg': '🖼️', 'gif': '🖼️', 'svg': '🖼️',
            'cs': '🟣', 'cpp': '🔵', 'c': '🔵', 'h': '🔴',
            'java': '☕', 'xml': '📑', 'yaml': '🔧', 'yml': '🔧',
            'sh': '🐚', 'bat': '🪟', 'sqlite': '🗄️', 'db': '🗄️'
        };
        return icons[ext] || '📄';
    }

    function updateUrl(filename) {
        const newUrl = `/${filename}`;
        window.history.pushState({path: newUrl}, '', newUrl);
    }

    async function copyCode() {
        if (!lastContent) return;
        await navigator.clipboard.writeText(lastContent);
        const btn = document.getElementById('copy-btn');
        btn.innerText = "✅ Скопійовано!";
        setTimeout(() => btn.innerText = "📋 Копіювати код", 2000);
    }

    async function loadFile(name) {
        currentFile = name;
        document.getElementById('current-filename').innerText = name;
        updateUrl(name);
        renderTree();

        const res = await fetch(`/api/content/${name}`);
        const data = await res.json();
        
        const codeContainer = document.getElementById('code-container');
        const imgContainer = document.getElementById('image-container');
        const copyBtn = document.getElementById('copy-btn');

        if (data.is_image) {
            codeContainer.style.display = 'none';
            copyBtn.style.display = 'none';
            imgContainer.style.display = 'block';
            document.getElementById('image-viewer').src = data.url;
            lastContent = "";
        } else {
            imgContainer.style.display = 'none';
            codeContainer.style.display = 'block';
            copyBtn.style.display = 'block';
            
            const codeBlock = document.getElementById('code-block');
            const preBlock = document.getElementById('pre-block');
            
            lastContent = data.content;
            codeBlock.textContent = data.content;
            
            if (data.changed_lines && data.changed_lines.length > 0) {
                preBlock.setAttribute('data-line', data.changed_lines.join(','));
            } else {
                preBlock.removeAttribute('data-line');
            }
            Prism.highlightElement(codeBlock);
        }
    }

    function renderTree() {
        const root = {};
        
        fileList.forEach(path => {
            const parts = path.split('/');
            let current = root;
            parts.forEach((part, i) => {
                if (i === parts.length - 1) {
                    current[part] = path;
                } else {
                    current[part] = current[part] || {};
                    current = current[part];
                }
            });
        });

        const container = document.getElementById('file-list');
        container.innerHTML = '';

        function buildNode(node, currentPath, level, parentElement) {
            const keys = Object.keys(node).sort((a, b) => {
                const isFolderA = typeof node[a] === 'object';
                const isFolderB = typeof node[b] === 'object';
                if (isFolderA && !isFolderB) return -1;
                if (!isFolderA && isFolderB) return 1;
                return a.localeCompare(b);
            });

            keys.forEach(key => {
                const isFolder = typeof node[key] === 'object';
                const fullPath = currentPath ? `${currentPath}/${key}` : key;
                
                const itemDiv = document.createElement('div');
                itemDiv.className = 'tree-item';
                itemDiv.style.paddingLeft = `${level * 15 + 8}px`;
                
                if (isFolder) {
                    const isOpen = openFolders.has(fullPath);
                    itemDiv.innerHTML = `
                        <div class="tree-label">
                            <span class="tree-arrow">${isOpen ? '▼' : '▶'}</span>
                            <span class="tree-icon">📁</span> ${key}
                        </div>
                    `;
                    itemDiv.onclick = () => {
                        if (isOpen) openFolders.delete(fullPath);
                        else openFolders.add(fullPath);
                        renderTree();
                    };
                    parentElement.appendChild(itemDiv);
                    
                    const childrenContainer = document.createElement('div');
                    childrenContainer.style.display = isOpen ? 'block' : 'none';
                    parentElement.appendChild(childrenContainer);
                    
                    buildNode(node[key], fullPath, level + 1, childrenContainer);
                } else {
                    const fileFullPath = node[key];
                    const icon = getFileIcon(key);
                    
                    if (fileFullPath === currentFile) itemDiv.classList.add('active');
                    
                    itemDiv.innerHTML = `
                        <div class="tree-label" title="${key}">
                            <span class="tree-arrow"></span>
                            <span class="tree-icon">${icon}</span> ${key}
                        </div>
                        <a href="/raw/${fileFullPath}" download="${key}" class="dl-btn" title="Завантажити файл" onclick="event.stopPropagation()">📥</a>
                    `;
                    itemDiv.onclick = () => loadFile(fileFullPath);
                    parentElement.appendChild(itemDiv);
                }
            });
        }
        
        buildNode(root, '', 0, container);
    }

    async function fetchFiles() {
        const res = await fetch('/api/files');
        const newFiles = await res.json();
        
        if (JSON.stringify(newFiles) !== JSON.stringify(fileList)) {
            fileList = newFiles;
            
            if (!currentFile && "{{ initial_file }}") {
                const parts = "{{ initial_file }}".split('/');
                let pathAcc = "";
                for(let i=0; i<parts.length-1; i++){
                    pathAcc = pathAcc ? `${pathAcc}/${parts[i]}` : parts[i];
                    openFolders.add(pathAcc);
                }
            }
            renderTree();
        }
    }

    window.onload = async () => {
        await fetchFiles();
        const initialFile = "{{ initial_file }}";
        if (initialFile && fileList.includes(initialFile)) {
            loadFile(initialFile);
        }
    };

    setInterval(async () => {
        await fetchFiles();
        if(currentFile) {
            const res = await fetch(`/api/check_update/${currentFile}`);
            const data = await res.json();
            if(data.updated) loadFile(currentFile);
        }
    }, 2000);
</script>

</body>
</html>
"""

# --- API МАРШРУТИ ---
@app.route('/api/files')
def api_files():
    return jsonify(get_files())

@app.route('/api/content/<path:filename>')
def api_content(filename):
    try:
        path = os.path.join(ROOT_DIR, filename)
        mime, _ = mimetypes.guess_type(path)
        
        if mime and mime.startswith('image/'):
            return jsonify({"is_image": True, "url": f"/raw/{filename}"})
            
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        
        changed_lines = get_changed_lines(filename, content)
        return jsonify({"is_image": False, "content": content, "changed_lines": changed_lines})
    except Exception as e:
        return jsonify({"is_image": False, "content": f"Помилка: {str(e)}", "changed_lines": []})

@app.route('/api/check_update/<path:filename>')
def check_update(filename):
    path = os.path.join(ROOT_DIR, filename)
    if not os.path.exists(path): return jsonify({"updated": False})
    
    mtime = os.path.getmtime(path)
    updated = False
    if filename not in last_mtime or mtime > last_mtime[filename]:
        last_mtime[filename] = mtime
        updated = True
    return jsonify({"updated": updated})

@app.route('/raw/<path:filename>')
def raw_file(filename):
    return send_file(os.path.join(ROOT_DIR, filename), as_attachment=True)

# --- UI МАРШРУТИ ---
@app.route('/')
@app.route('/<path:filename>')
def index(filename=""):
    port_str = f":{PORT}" if PORT != 80 else ""
    return render_template_string(
        HTML_TEMPLATE, 
        domain_url=f"http://{DOMAIN}.local{port_str}",
        ip_url=f"http://{get_local_ip()}{port_str}",
        initial_file=filename
    )

def start_bonjour():
    local_ip = get_local_ip()
    desc = {'path': '/'}
    info = ServiceInfo(
        "_http._tcp.local.",
        f"{DOMAIN}._http._tcp.local.",
        addresses=[socket.inet_aton(local_ip)],
        port=PORT,
        properties=desc,
        server=f"{DOMAIN}.local.",
    )
    zc = Zeroconf()
    zc.register_service(info)
    return zc, info

if __name__ == '__main__':
    zc, info = start_bonjour()
    port_str = f":{PORT}" if PORT != 80 else ""
    print(f"--- Code Stream Started ---")
    print(f"Sharing folder: {ROOT_DIR}")
    print(f"Local URL: http://{DOMAIN}.local{port_str}")
    print(f"IP URL: http://{get_local_ip()}{port_str}")
    
    try:
        app.run(host='0.0.0.0', port=PORT, debug=False)
    finally:
        zc.unregister_service(info)
        zc.close()