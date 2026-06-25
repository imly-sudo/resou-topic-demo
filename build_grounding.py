#!/usr/bin/env python3
# 每天由 GitHub Actions 跑: 拉最近历史微博热搜 -> 12品类候选 -> GLM洗净 -> grounding.json
import json, re, os, datetime, urllib.request, concurrent.futures

WEIBO  = "https://raw.githubusercontent.com/iiecho1/hot_searches_for_apps/main/archives/%E5%BE%AE%E5%8D%9A"
DOUYIN = "https://raw.githubusercontent.com/iiecho1/hot_searches_for_apps/main/archives/%E6%8A%96%E9%9F%B3"
KEY = os.environ["GLM_API_KEY"]

CATS = {
 "新能源车":["新能源","电动车","电车","续航","智驾","智能驾驶","SU7","小米汽车","理想","蔚来","问界","尊界","比亚迪","极氪","小鹏","充电","换电","试驾","交付","特斯拉","固态电池","车型","汽车"],
 "数码科技":["手机","芯片","iPhone","鸿蒙","华为Mate","小米1","荣耀","OPPO","vivo","骁龙","英伟达","显卡","电脑","平板","耳机","折叠屏","DeepSeek","大模型","机器人","发布会","iOS","AI"],
 "社会民生":["通报","回应","警方","民警","事故","坠","遇难","身亡","判刑","法院","判","曝光","维权","欠薪","涨价","高考","台风","暴雨","地震","失联","救援","诈骗","电诈","立案","调查","处罚"],
 "影视综艺":["电影","上映","开播","收视","综艺","央视","导演","票房","定档","杀青","预告","剧","播出","卫视","春晚"],
 "明星八卦":["官宣","恋情","分手","塌房","工作室","代言","路透","绯闻","结婚","离婚","恋爱","粉丝","生图","演唱会"],
 "体育赛事":["夺冠","决赛","世界杯","国足","球星","冠军","晋级","奥运","联赛","球员","进球","退役","赛","梅西","C罗","樊振东"],
 "财经政策":["股市","A股","楼市","房价","降息","补贴","黄金","GDP","油价","基金","汇率","经济","降准","央行","税"],
 "美食生活":["美食","咖啡","奶茶","探店","餐厅","外卖","家居","旅游","景区","网红店","榴莲","螺蛳粉","食堂"],
 "游戏动漫":["游戏","电竞","版本","上线","动漫","二次元","原神","英雄联盟","王者","主机","Steam","皮肤","赛季"],
 "美妆时尚":["彩妆","护肤","口红","粉底","穿搭","时装","奢侈品","秀场","平替","成分","防晒","香水"],
 "母婴亲子":["母婴","宝宝","孩子","育儿","奶粉","幼儿园","家长","亲子","产妇","小学生","二胎"],
 "健康养生":["养生","疾病","医院","医生","减肥","睡眠","体检","中医","近视","血压","熬夜","猝死"],
}

def fetch(url):
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"}), timeout=20).read().decode("utf-8")
    except Exception:
        return ""

def day_urls(base, days):
    today = datetime.date.today()
    out = []
    for i in range(days):
        d = today - datetime.timedelta(days=i)
        out.append((d, f"{base}/{d.year}/{d.month:02d}/{d.year}-{d.month:02d}-{d.day:02d}.md"))
    return out

def pull_history(days=100):
    rows = []  # (title, rank, date_str, platform)
    # 微博: band_rank 在 url 里, 无则记 99
    wb = day_urls(WEIBO, days)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        for (d, _), txt in zip(wb, ex.map(lambda u: fetch(u[1]), wb)):
            for m in re.finditer(r"\+\s*\[(.*?)\]\(([^)]*)\)", txt):
                bm = re.search(r"band_rank=(\d+)", m.group(2))
                rows.append((m.group(1).strip(), int(bm.group(1)) if bm else 99, str(d), "微博"))
    # 抖音: 无 band_rank, 文件内行序即排名(遇 # 头重置)
    dy = day_urls(DOUYIN, days)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        for (d, _), txt in zip(dy, ex.map(lambda u: fetch(u[1]), dy)):
            rank = 0
            for line in txt.splitlines():
                if line.startswith("#"):
                    rank = 0
                    continue
                m = re.match(r"\+\s*\[(.*?)\]\(", line)
                if m:
                    rank += 1
                    rows.append((m.group(1).strip(), rank, str(d), "抖音"))
    return rows

def glm(prompt, temp=0.3):
    b = json.dumps({"model":"glm-4-flash","temperature":temp,"messages":[{"role":"user","content":prompt}]}).encode()
    r = urllib.request.Request("https://open.bigmodel.cn/api/paas/v4/chat/completions", data=b,
        headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"})
    c = json.loads(urllib.request.urlopen(r, timeout=80).read())["choices"][0]["message"]["content"].strip()
    return re.sub(r"^```\w*|```$", "", c, flags=re.M).strip()

def src_of(word, srcs):
    # 把(可能被GLM精简过的)清洗词映射回原始平台来源: 微博 / 抖音 / 双
    if word in srcs:
        s = srcs[word]
    else:
        s = set()
        for t, ps in srcs.items():
            if word and (word in t or t in word):
                s |= ps
    if not s:
        return ""
    return "双" if len(s) > 1 else next(iter(s))

def clean(cat, seeds, wb_words, dy_words, heat, srcs):
    # 分微博/抖音两组各自清洗 + 蒸馏命名"打榜公式"(带真实案例)
    p = (f'下面是"{cat}"品类真实上过热搜的词,分微博和抖音两组。做两件事:\n'
         f'1) 每组只保留真正属于该品类、对选题有参考价值的词,剔噪声。微博≤11,抖音≤5。\n'
         f'2) 从这些词归纳5-7个"打榜公式"(该品类容易上热搜的套路)。每个公式给: name(4-8字好记的名字,如"价格直给"), '
         f'template(一句话模板,讲清这套路怎么造词), examples(2-3个,必须从上面给的词里原样挑,不许编)。\n'
         f'微博词:{json.dumps(wb_words, ensure_ascii=False)} 抖音词:{json.dumps(dy_words, ensure_ascii=False)} '
         '只输出JSON: {"weibo":[...],"douyin":[...],"formulas":[{"name":"","template":"","examples":["",""]}]}')
    try:
        r = json.loads(glm(p))
        cw = [{"w": w, "src": src_of(w, srcs)} for w in r.get("weibo", [])[:11]] \
           + [{"w": w, "src": src_of(w, srcs)} for w in r.get("douyin", [])[:5]]
        formulas = []
        for f in (r.get("formulas") or [])[:7]:
            name = (f.get("name") or "").strip()
            exs = []
            for w in (f.get("examples") or [])[:3]:
                w = (w or "").strip(); s = src_of(w, srcs)
                if w and s:  # 只留能映射回真实上榜词的案例, 编的丢掉
                    exs.append({"w": w, "src": s})
            if name and exs:
                formulas.append({"name": name[:12], "template": (f.get("template") or "").strip()[:40], "examples": exs})
        return cat, {"heat":heat, "seeds":seeds, "clean_words":cw, "formulas":formulas}
    except Exception:
        cw = [{"w": w, "src": src_of(w, srcs)} for w in (wb_words[:9] + dy_words[:5])]
        return cat, {"heat":heat, "seeds":seeds, "clean_words":cw, "formulas":[]}

def main():
    rows = pull_history()
    print(f"pulled {len(rows)} history rows")
    recent_cut = str(datetime.date.today() - datetime.timedelta(days=30))
    cands = {}
    for cat, seeds in CATS.items():
        srcs = {}; wb_rank = {}; dy_rank = {}
        for t, rk, d, p in rows:
            if any(s in t for s in seeds):
                srcs.setdefault(t, set()).add(p)
                tgt = wb_rank if p == "微博" else dy_rank
                if t not in tgt or rk < tgt[t]:
                    tgt[t] = rk
        heat = len(set(t for (t, rk, d, p) in rows if d >= recent_cut and any(s in t for s in seeds)))
        # 两平台各取 top 分别送清洗(微博快照多会淹没抖音, 必须分组)
        wb_top = [w for w, _ in sorted(wb_rank.items(), key=lambda x: x[1])[:30]]
        dy_top = [w for w, _ in sorted(dy_rank.items(), key=lambda x: x[1])[:18]]
        cands[cat] = (seeds, wb_top, dy_top, heat, srcs)
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        futs = [ex.submit(clean, c, s, wb, dy, h, sm) for c, (s, wb, dy, h, sm) in cands.items()]
        for f in concurrent.futures.as_completed(futs):
            c, v = f.result()
            out[c] = v
            print(f"[{c}] heat={v['heat']} words={len(v['clean_words'])}")
    out["_updated"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    json.dump(out, open("grounding.json", "w"), ensure_ascii=False, indent=2)
    print("wrote grounding.json")

if __name__ == "__main__":
    main()
