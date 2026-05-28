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
                
                const isVisible = el => {
                    const style = window.getComputedStyle(el);
                    if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 2 && rect.height > 2;
                };

                all.forEach((el, index) => {
                    if (!isVisible(el)) return;
                    
                    const tag = el.tagName.toLowerCase();
                    const type = (el.getAttribute('type') || '').toLowerCase();
                    
                    // Filter non-functional links
                    if (tag === 'a') {
                        const href = el.getAttribute('href') || '';
                        const onclick = el.getAttribute('onclick') || '';
                        if (!onclick && (!href || href === '#' || href.startsWith('javascript:void'))) return;
                        if (!el.innerText.trim() && !el.getAttribute('title') && !el.querySelector('img')) return;
                    }

                    // Filter hidden inputs
                    if (tag === 'input' && type === 'hidden') return;

                    const attrs = {};
                    for (const attr of el.attributes) {
                        attrs[attr.name] = attr.value;
                    }
                    
                    let kind = 'field';
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
                        text: (el.innerText || el.value || '').trim().slice(0, 200),
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
