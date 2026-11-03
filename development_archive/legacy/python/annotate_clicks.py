"""Draw an observed click plan on an existing screenshot; no desktop access."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser(description='在已有截图上框选点击位置，不读取或操作桌面')
    parser.add_argument('image', type=Path)
    parser.add_argument('plan', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding='utf-8-sig'))
    image = Image.open(args.image).convert('RGB')
    if list(image.size) != plan['screenshot_size']:
        raise ValueError('截图大小与点击计划不一致，禁止套用旧坐标')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 14)
    for i, target in enumerate(plan['targets'], 1):
        x, y, w, h = target['box']
        if x < 0 or y < 0 or x + w > image.width or y + h > image.height:
            raise ValueError('目标框超出截图范围')
        draw.rectangle((x, y, x + w, y + h), outline='#ef233c', width=3)
        draw.rounded_rectangle((x, y - 22, x + 22, y), radius=3, fill='#ef233c')
        draw.text((x + 5, y - 22), str(i), font=font, fill='white')
    draw.rounded_rectangle((14, 365, image.width - 14, 423), radius=8, fill='#ffffff')
    draw.text((23, 373), '观察时建议：依次点击 1 → 2 → 3', font=font, fill='#173226')
    draw.text((23, 396), '历史截图标注；操作后需重新核对', font=font, fill='#795800')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output)
    print(args.output)


if __name__ == '__main__':
    main()
