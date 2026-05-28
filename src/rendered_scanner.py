import sys
import json
import argparse
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

def scan_rendered_page(url, output_path):
    print(f"Scanning rendered page: {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = context.new_page()
        
        # Navigate to the target page
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
        except Exception as e:
            print(f"Navigation failed: {e}")
            browser.close()
            return False

        # Extract elements using JS to get the computed state
        elements = page.evaluate("""
            () => {
                const results = [];
                const all = document.querySelectorAll('input, button, a, select, textarea, [onclick]');
                
                all.forEach((el, index) => {
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) return; // Skip hidden
                    
                    const attrs = {};
                    for (const attr of el.attributes) {
                        attrs[attr.name] = attr.value;
                    }
                    
                    let kind = 'field';
                    const tag = el.tagName.toLowerCase();
                    const type = (el.getAttribute('type') || '').toLowerCase();
                    
                    if (tag === 'a') kind = 'link';
                    else if (tag === 'button' || type === 'button' || type === 'submit') kind = 'button';
                    else if (type === 'file') kind = 'file';
                    
                    // Simple selector generation
                    let selector = tag;
                    if (el.id) selector += '#' + el.id;
                    else if (el.name) selector += `[name="${el.name}"]`;
                    
                    results.push({
                        kind: kind,
                        tag: tag,
                        attributes: attrs,
                        text: el.innerText || el.value || '',
                        locator: selector,
                        rect: {x: rect.x, y: rect.y, w: rect.width, h: rect.height}
                    });
                });
                return results;
            }
        """)
        
        result_data = {
            "root": url,
            "pages": [
                {
                    "source": url,
                    "elements": elements
                }
            ]
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)
            
        browser.close()
        return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()
    scan_rendered_page(args.url, args.output)
