#!/usr/bin/env python3
"""生成工作台图标。

图形就是这个工具要揭示的那件事：一条记忆的摘要，实心是真的，
空心是占位符——两者一样长，肉眼分不出，检索时却天差地别。
配色直接取自面板的 CSS 变量，图标和界面是同一套视觉。
"""
from PIL import Image, ImageDraw

S      = 1024
SS     = 4                      # 超采样倍数，PIL 的圆角矩形自身不抗锯齿
GROUND = (16, 20, 28, 255)      # --ground
DONE   = (95, 208, 168, 255)    # --done
LIVE   = (111, 168, 255, 255)   # --live
FAINT  = (90, 101, 120, 255)    # --faint
FAINT_S = (124, 138, 160, 255)  # 小尺寸下提亮，否则空心条在深底上直接消失

# (相对长度, 状态)  状态 2=摘要已补好 1=正在补 0=还是占位符
BARS = [
    (1.00, 2),
    (0.82, 2),
    (0.58, 1),
    (0.94, 0),
    (0.71, 0),
]

# 16/32 像素下五条会糊成一片，空心条的描边不足 1px。
# 简化成三条、加粗、放大占比——这是 macOS 图标的常规做法，不是偷懒。
BARS_SMALL = [
    (1.00, 2),
    (0.66, 1),
    (0.88, 0),
]


def ground(n, pad, body, r):
    """深色底 + 一层极淡的竖向渐变。纯平色在深色壁纸上会糊成一团。"""
    g = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    grad = Image.new("RGBA", (1, n))
    for y in range(n):
        t = y / n
        grad.putpixel((0, y), (
            int(GROUND[0] + 14 * (1 - t)),
            int(GROUND[1] + 16 * (1 - t)),
            int(GROUND[2] + 20 * (1 - t)), 255))
    grad = grad.resize((n, n))
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [pad, pad, pad + body, pad + body], radius=r, fill=255)
    g.paste(grad, (0, 0), mask)
    return g


def draw(size):
    n = size * SS
    pad = int(n * 0.098)                 # macOS 图标留白约 10%
    body = n - pad * 2
    r = int(body * 0.225)                # 圆角约边长的 22.5%
    img = ground(n, pad, body, r)
    d = ImageDraw.Draw(img)

    small = size <= 32
    bars = BARS_SMALL if small else BARS
    hollow = FAINT_S if small else FAINT
    area = int(body * (0.70 if small else 0.62))
    left = pad + (body - area) // 2
    top = pad + (body - area) // 2
    bar = area / (len(bars) + (len(bars) - 1) * 0.52)
    gap = bar * 0.52
    br = bar / 2                          # 全圆头，像一条文本行
    stroke = max(SS, int(bar * (0.20 if small else 0.16)))

    for i, (w, st) in enumerate(bars):
        y = top + i * (bar + gap)
        box = [left, y, left + area * w, y + bar]
        if st == 2:
            d.rounded_rectangle(box, radius=br, fill=DONE)
        elif st == 1:
            d.rounded_rectangle(box, radius=br, fill=LIVE)
        else:
            d.rounded_rectangle(box, radius=br, outline=hollow, width=stroke)

    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    import os, shutil
    shutil.rmtree("memmy.iconset", ignore_errors=True)
    os.makedirs("memmy.iconset")
    for px in (16, 32, 128, 256, 512):    # iconutil 要求的全套尺寸
        draw(px).save(f"memmy.iconset/icon_{px}x{px}.png")
        draw(px * 2).save(f"memmy.iconset/icon_{px}x{px}@2x.png")
    draw(1024).save("preview.png")
    # 小尺寸实际观感：放大到 512 好肉眼检查
    for px in (16, 32, 64):
        draw(px).resize((512, 512), Image.NEAREST).save(f"check-{px}.png")
    print("已生成")
