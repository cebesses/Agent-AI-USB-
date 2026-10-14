"""
Agent IA USB Vision Pipeline
Sends images to the llama.cpp server, which runs the model's vision
projector (mmproj) for image understanding.
"""
import base64, json, os, subprocess
from pathlib import Path

class VisionPipeline:
    def __init__(self, llama_server_url="http://127.0.0.1:8080"):
        self.server_url = llama_server_url

    def is_available(self):
        """Check if vision projector is loaded."""
        try:
            import urllib.request
            resp = urllib.request.urlopen(f"{self.server_url}/health", timeout=5)
            return resp.status == 200
        except Exception:
            return False

    def analyze_image(self, image_path, prompt="Describe this image in detail."):
        """Analyze an image using the vision model."""
        import urllib.request

        fp = Path(image_path)
        if not fp.exists():
            return {"error": f"Image not found: {image_path}"}

        # Read and encode image
        with open(fp, "rb") as f:
            img_data = base64.b64encode(f.read()).decode()

        # Determine MIME type
        ext = fp.suffix.lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}
        mime = mime_map.get(ext, "image/png")

        payload = json.dumps({
            "model": "local-model",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_data}"}},
                    {"type": "text", "text": prompt + "\nResponde en español, de forma concisa."}
                ]
            }],
            "max_tokens": 600,
            "temperature": 0.3,
            "chat_template_kwargs": {"enable_thinking": False},
        }).encode()

        req = urllib.request.Request(
            f"{self.server_url}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            # CPU-only image encoding can take a few minutes
            resp = urllib.request.urlopen(req, timeout=900)
            result = json.loads(resp.read())
            return {"text": result["choices"][0]["message"]["content"]}
        except Exception as e:
            return {"error": str(e)}

    def analyze_screenshot(self, prompt="What is on this screenshot?"):
        """Capture and analyze the screen."""
        # Save screenshot
        screenshot_path = Path(os.environ.get("TEMP", ".")) / "agent_ia_usb_screenshot.png"
        try:
            subprocess.run([
                "powershell", "-NoProfile", "-Command",
                f'Add-Type -AssemblyName System.Windows.Forms; '
                f'[System.Windows.Forms.Screen]::PrimaryScreen | ForEach-Object {{ '
                f'$bmp = New-Object System.Drawing.Bitmap($_.Bounds.Width, $_.Bounds.Height); '
                f'$gfx = [System.Drawing.Graphics]::FromImage($bmp); '
                f'$gfx.CopyFromScreen($_.Bounds.Location, [System.Drawing.Point]::Empty, $_.Bounds.Size); '
                f'$bmp.Save("{screenshot_path}") }}'
            ], capture_output=True, timeout=10)

            if screenshot_path.exists():
                return self.analyze_image(str(screenshot_path), prompt)
        except Exception as e:
            return {"error": f"Screenshot capture failed: {e}"}

        return {"error": "Could not capture screenshot"}