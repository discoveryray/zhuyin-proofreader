from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from docx import Document

RULE_FIELDS = [
    "rule_id","priority","mode","target_char","phrase","expected_reading",
    "excluded_phrases","source","source_url","note","pdf_contains","evidence_level",
]
CONSTRAINT_FIELDS = ["char","allowed_readings","source","source_section","note"]
SOURCE = "統一用字手冊113.10.04.docx"
PRIORITY_EXACT = 10000
PRIORITY_DETAIL = 10100

BPMF_RE = re.compile(r"[\u3105-\u312fㄧ]+[˙ˊˇˋ]?")


def norm_bpmf(s: str) -> str:
    s = str(s or "").strip().replace("一", "ㄧ").replace("厶", "ㄙ")
    s = re.sub(r"\s+", "", s)
    if not s:
        return ""
    # source handbook commonly places the neutral-tone dot after the syllable;
    # runtime normal form places it before the syllable.
    if s.endswith("˙"):
        s = "˙" + s[:-1]
    return s


def split_bpmf_cell(s: str) -> list[str]:
    s = str(s or "").replace("一", "ㄧ").replace("厶", "ㄙ")
    vals = []
    for m in BPMF_RE.finditer(s):
        v = norm_bpmf(m.group(0))
        if v:
            vals.append(v)
    return vals


def clean_phrase(s: str) -> str:
    s = str(s or "")
    s = s.replace("「", "").replace("」", "").replace("【", "").replace("】", "")
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[。；;，,：:].*$", "", s)
    return s.strip("、/ ")


def quoted_targets(s: str) -> list[str]:
    return re.findall(r"「([^」]+)」", str(s or ""))


def add_rule(rows: list[dict], rule_id: str, target: str, phrase: str, reading: str,
             section: str, note: str = "", priority: int = PRIORITY_EXACT, mode: str = "exact"):
    target = target.strip(); phrase = clean_phrase(phrase); reading = norm_bpmf(reading)
    if not target or not phrase or not reading:
        return
    rows.append({
        "rule_id": rule_id,
        "priority": priority,
        "mode": mode,
        "target_char": target,
        "phrase": phrase,
        "expected_reading": reading,
        "excluded_phrases": "",
        "source": SOURCE,
        "source_url": "",
        "note": f"{section}｜{note}".strip("｜"),
        "pdf_contains": "",
        "evidence_level": "handbook_highest",
    })


def target_char_from_segment(segment: str, quoted: str | None) -> str | None:
    if quoted:
        if len(quoted) == 1:
            return quoted
        # repeated same glyph, e.g. 纍纍 / 莘莘 / 比比: one rule safely applies to both.
        if len(set(quoted)) == 1:
            return quoted[0]
        return None
    return None


def import_table_4_5_6(doc: Document, rules: list[dict]):
    for ti in (4, 5, 6):
        table = doc.tables[ti]
        section = {4:"容易錯的注音",5:"同字異義",6:"同字詞性相異"}[ti]
        for ri, row in enumerate(table.rows[1:], 1):
            cells = [" ".join(c.text.split()) for c in row.cells]
            if len(cells) < 2 or not cells[0] or "正確音" in cells[1]:
                continue
            field, correct = cells[0], cells[1]
            readings = split_bpmf_cell(correct)
            if not readings:
                continue
            # Split only the explicit multi-example cell used by 刻度、雕刻.
            segments = [x.strip() for x in re.split(r"[、]", field) if x.strip()]
            if len(segments) > 1 and len(readings) == 1:
                for si, seg in enumerate(segments, 1):
                    qs = quoted_targets(seg)
                    target = target_char_from_segment(seg, qs[0] if qs else None)
                    if target:
                        add_rule(rules, f"HB-T{ti}-R{ri:03d}-{si}", target, seg, readings[0], section, cells[-1])
                continue

            qs = quoted_targets(field)
            if not qs:
                # One explicit unquoted source row: 游酢, where the note itself discusses 酢.
                if clean_phrase(field) == "游酢" and readings:
                    add_rule(rules, f"HB-T{ti}-R{ri:03d}", "酢", "游酢", readings[0], section, cells[-1])
                continue
            q = qs[0]
            phrase = clean_phrase(field)
            if len(q) == 1:
                # Some source cells hold two acceptable readings for the same phrase/meaning.
                # Emit separate same-priority rules so the runtime abstains instead of guessing.
                for k, rd in enumerate(readings, 1):
                    add_rule(rules, f"HB-T{ti}-R{ri:03d}-{k}", q, phrase, rd, section, cells[-1])
            elif len(set(q)) == 1:
                # Repeated same character; a single reading applies to both positions if source gives one.
                if len(readings) == 1:
                    add_rule(rules, f"HB-T{ti}-R{ri:03d}", q[0], phrase, readings[0], section, cells[-1])
            elif len(q) == len(readings):
                for k, (ch, rd) in enumerate(zip(q, readings), 1):
                    add_rule(rules, f"HB-T{ti}-R{ri:03d}-{k}", ch, phrase, rd, section, cells[-1])


def import_constraints_table10(doc: Document) -> list[dict]:
    out=[]
    table=doc.tables[10]
    for ri,row in enumerate(table.rows[1:],1):
        cells=[" ".join(c.text.split()) for c in row.cells]
        ch=(cells[0] if cells else "").strip()
        if len(ch)!=1:
            continue
        vals=[]
        for part in re.split(r"[、;；]", cells[1] if len(cells)>1 else ""):
            v=norm_bpmf(part)
            if v and v not in vals:
                vals.append(v)
        if not vals:
            continue
        out.append({
            "char":ch,
            "allowed_readings":"；".join(vals),
            "source":SOURCE,
            "source_section":"容易錯的注音－單字正誤音表",
            "note":f"手冊列為正確音：{'、'.join(vals)}；錯誤音：{cells[2] if len(cells)>2 else ''}",
        })
    # Narrative conclusion explicitly gives one global pronunciation for 纍.
    out.append({"char":"纍","allowed_readings":"ㄌㄟˊ","source":SOURCE,
                "source_section":"語文組統一注音1－果實纍纍","note":"手冊結論：【纍】注音為ㄌㄟˊ。"})
    return out


def import_narrative_examples(rules: list[dict]):
    items = [
        ("悶","煩悶","ㄇㄣˋ","心裡煩悶"),("悶","悶熱","ㄇㄣ","被罩住／悶熱"),
        ("吐","吐痰","ㄊㄨˇ","從口中出"),("吐","吐血","ㄊㄨˋ","從胃中出"),
        ("滑","滑稽","ㄍㄨˇ","文言文用法"),("滑","滑稽","ㄏㄨㄚˊ","白話文用法；同形詞義需人工判斷"),
        ("強","勉強","ㄑㄧㄤˇ","硬要、迫使"),("強","強壯","ㄑㄧㄤˊ","有力、健壯"),
        ("姊","姊","ㄗˇ","手冊正文：姊音ㄗˇ"),("姊","姊姊","ㄐㄧㄝˇ","僅姊姊通姐姐時可用"),
        ("扛","扛桌子","ㄍㄤ","用雙手舉"),
        ("折","折斷","ㄓㄜˊ","弄斷"),("折","折腰","ㄓㄜˊ","彎"),("折","曲折","ㄓㄜˊ","彎曲"),("折","折壽","ㄓㄜˊ","損失"),
        ("折","折本","ㄕㄜˊ","虧損"),("折","折錢","ㄕㄜˊ","虧損"),("折","折騰","ㄓㄜ","翻轉、迴旋"),("折","折跟頭","ㄓㄜ","翻轉、迴旋"),
        ("骨","筋骨","ㄍㄨˇ","主要讀音"),("骨","骨骼","ㄍㄨˇ","主要讀音"),("骨","骨肉","ㄍㄨˇ","主要讀音"),("骨","骨頭","ㄍㄨˊ","限用骨頭"),("骨","硬骨頭","ㄍㄨˊ","限用骨頭"),("骨","懶骨頭","ㄍㄨˊ","限用骨頭"),("骨","骨朵兒","ㄍㄨ","限用詞"),("骨","骨碌","ㄍㄨ","限用詞"),
        ("撇","撇嘴","ㄆㄧㄝˇ","撇嘴／一撇"),("撇","一撇","ㄆㄧㄝˇ","撇嘴／一撇"),("撇","撇下","ㄆㄧㄝ","餘義"),("撇","撇棄","ㄆㄧㄝ","餘義"),("撇","撇清","ㄆㄧㄝ","餘義"),("撇","撇開","ㄆㄧㄝ","餘義"),
        ("暈","頭暈眼花","ㄩㄣ","與頭昏有關"),("暈","暈車","ㄩㄣ","與頭昏有關"),("暈","暈倒","ㄩㄣ","與頭昏有關"),("暈","月暈","ㄩㄣˋ","名詞"),("暈","燈暈","ㄩㄣˋ","名詞"),("暈","酒暈","ㄩㄣˋ","名詞"),("暈","血暈","ㄩㄣˋ","名詞"),
        ("更","自力更生","ㄍㄥˋ","愈、再"),("更","更好","ㄍㄥˋ","愈、再"),("更","更生人","ㄍㄥˋ","愈、再"),("更","變更","ㄍㄥ","變換、代替"),("更","更夫","ㄍㄥ","變換、代替"),("更","三更半夜","ㄍㄥ","變換、代替／更次"),("更","少不更事","ㄍㄥ","變換、代替"),
        ("漲","熱漲冷縮","ㄓㄤˋ","體積膨大"),("漲","漲潮","ㄓㄤˇ","標準線增高"),("漲","漲價","ㄓㄤˇ","標準線增高"),("漲","水漲船高","ㄓㄤˇ","標準線增高"),
        ("燥","乾燥","ㄗㄠˋ","乾、缺乏水分"),("燥","枯燥","ㄗㄠˋ","乾、缺乏水分"),("燥","燥熱","ㄗㄠˋ","乾、缺乏水分"),("燥","天乾物燥","ㄗㄠˋ","乾、缺乏水分"),("燥","肉燥","ㄙㄠˋ","剁成細碎的肉；源檔字形『厶』依同表ㄙ音分類正規化為ㄙ"),("燥","燥子豆腐","ㄙㄠˋ","剁成細碎的肉；源檔字形『厶』依同表ㄙ音分類正規化為ㄙ"),
        ("磨","石磨","ㄇㄛˋ","以磨子碾碎／名詞"),("磨","磨麵","ㄇㄛˋ","以磨子碾碎"),("磨","磨豆腐","ㄇㄛˋ","以磨子碾碎"),("磨","磨刀","ㄇㄛˊ","其餘動作"),("磨","磨牙","ㄇㄛˊ","其餘動作"),("磨","磨練","ㄇㄛˊ","其餘動作"),("磨","好事多磨","ㄇㄛˊ","其餘動作"),
        ("轉","轉彎","ㄓㄨㄢˇ","改變方向"),("轉","旋轉","ㄓㄨㄢˇ","手冊本節列為改變方向義的例"),("轉","轉圈兒","ㄓㄨㄢˋ","有軸可繞、可回到原點"),("轉","暈頭轉向","ㄓㄨㄢˋ","有軸可繞、可回到原點"),("轉","地球自轉","ㄓㄨㄢˋ","有軸可繞、可回到原點"),("轉","公轉","ㄓㄨㄢˋ","有軸可繞、可回到原點"),
        ("冠","鳳冠","ㄍㄨㄢ","帽子／頂端"),("冠","皇冠","ㄍㄨㄢ","帽子／頂端"),("冠","怒髮衝冠","ㄍㄨㄢ","帽子／頂端"),("冠","花冠","ㄍㄨㄢ","頂端物"),("冠","雞冠","ㄍㄨㄢ","頂端物"),("冠","冠禮","ㄍㄨㄢˋ","成年禮"),("冠","弱冠","ㄍㄨㄢˋ","成年禮"),("冠","獨冠群雄","ㄍㄨㄢˋ","超越、領先"),("冠","豔冠群芳","ㄍㄨㄢˋ","超越、領先"),("冠","冠夫姓","ㄍㄨㄢˋ","加上"),("冠","冠罪名","ㄍㄨㄢˋ","加上"),("冠","冠軍","ㄍㄨㄢˋ","名次第一"),
    ]
    for i,(ch,ph,rd,note) in enumerate(items,1):
        add_rule(rules,f"HB-NARR-{i:03d}",ch,ph,rd,"一字多音審訂表整理",note,priority=PRIORITY_EXACT)


def import_detailed_sections(rules: list[dict]):
    # 語文組統一注音1-6 / 翰林國語科注音記要中可直接落成完整詞語規則的項目。
    details = [
        ("纍","果實纍纍","ㄌㄟˊ","語文組統一注音1－果實纍纍"),
        ("麗","高麗菜","ㄌㄧˊ","語文組統一注音2－高麗菜"),
        ("教","佛教","ㄐㄧㄠˋ","語文組統一注音3－教"),("教","教唆","ㄐㄧㄠˋ","語文組統一注音3－教"),("教","教育","ㄐㄧㄠˋ","語文組統一注音3－教"),("教","教材教法","ㄐㄧㄠˋ","語文組統一注音3－教"),("教","諄諄教誨","ㄐㄧㄠˋ","語文組統一注音3－教"),("教","因材施教","ㄐㄧㄠˋ","語文組統一注音3－教"),("教","教學","ㄐㄧㄠˋ","語文組統一注音3－教"),("教","教學相長","ㄐㄧㄠˋ","語文組統一注音3－教"),("教","教學研討會","ㄐㄧㄠˋ","語文組統一注音3－教"),("教","教學生","ㄐㄧㄠ","語文組統一注音3－教"),("教","教書匠","ㄐㄧㄠ","語文組統一注音3－教"),("教","教唱","ㄐㄧㄠ","語文組統一注音3－教"),("教","教書","ㄐㄧㄠ","語文組統一注音3－教"),
        ("兒","這兒","ㄦ","語文組統一注音4－兒化音"),("兒","鳥兒","ㄦ","語文組統一注音4－兒化音"),("兒","花兒","ㄦ","語文組統一注音4－兒化音"),("兒","草兒","ㄦ","語文組統一注音4－兒化音"),("兒","模特兒","ㄦ","語文組統一注音4－兒化音"),("兒","老頭兒","ㄦ","語文組統一注音4－兒化音"),("兒","美人兒","ㄦ","語文組統一注音4－兒化音"),("兒","拐彎兒","ㄦ","語文組統一注音4－兒化音"),("兒","找碴兒","ㄦ","語文組統一注音4－兒化音"),("兒","快快兒","ㄦ","語文組統一注音4－兒化音"),("兒","慢慢兒","ㄦ","語文組統一注音4－兒化音"),
        ("轉","轉動門把","ㄓㄨㄢˇ","語文組統一注音5－轉：改變方向／轉身活動"),("轉","轉動開關","ㄓㄨㄢˇ","語文組統一注音5－轉：改變方向／轉身活動"),("轉","目不轉睛","ㄓㄨㄢˇ","語文組統一注音5－轉"),("轉","轉頭","ㄓㄨㄢˇ","語文組統一注音5－轉"),("轉","天旋地轉","ㄓㄨㄢˇ","語文組統一注音5－轉"),
        ("轉","暈頭轉向","ㄓㄨㄢˋ","語文組統一注音5－轉"),("轉","公轉","ㄓㄨㄢˋ","語文組統一注音5－轉"),("轉","自轉","ㄓㄨㄢˋ","語文組統一注音5－轉"),("轉","打轉","ㄓㄨㄢˋ","語文組統一注音5－轉"),("轉","團團轉","ㄓㄨㄢˋ","語文組統一注音5－轉"),
        ("臭","臭味相投","ㄒㄧㄡˋ","語文組統一注音6－臭味相投"),
    ]
    for i,(ch,ph,rd,sec) in enumerate(details,1):
        add_rule(rules,f"HB-DETAIL-{i:03d}",ch,ph,rd,sec,"手冊明列／結論詞例。",priority=PRIORITY_DETAIL)


def import_light_tone_note(rules: list[dict]):
    sec="翰林國語科注音記要－輕聲區／本調區"
    # Exact phrase rules supported explicitly by the handbook table.
    phrase_rules = {
        # 頭
        "裡頭":("頭","˙ㄊㄡ"),"外頭":("頭","˙ㄊㄡ"),"跟頭":("頭","˙ㄊㄡ"),"罐頭":("頭","˙ㄊㄡ"),"饅頭":("頭","˙ㄊㄡ"),"念頭":("頭","˙ㄊㄡ"),"來頭":("頭","˙ㄊㄡ"),"苗頭":("頭","˙ㄊㄡ"),"石頭":("頭","˙ㄊㄡ"),"木頭":("頭","˙ㄊㄡ"),"手指頭":("頭","˙ㄊㄡ"),"翻跟頭":("頭","˙ㄊㄡ"),"拳頭":("頭","˙ㄊㄡ"),
        "山頭":("頭","ㄊㄡˊ"),"盡頭":("頭","ㄊㄡˊ"),"心頭":("頭","ㄊㄡˊ"),"眉頭":("頭","ㄊㄡˊ"),"矛頭":("頭","ㄊㄡˊ"),"鐘頭":("頭","ㄊㄡˊ"),"塊頭":("頭","ㄊㄡˊ"),"帶頭":("頭","ㄊㄡˊ"),"人頭":("頭","ㄊㄡˊ"),"水龍頭":("頭","ㄊㄡˊ"),
        # explicit light tone examples
        "對呀":("呀","˙ㄧㄚ"),"回家嘍":("嘍","˙ㄌㄡ"),
        "站著":("著","˙ㄓㄜ"),"迎著":("著","˙ㄓㄜ"),"想著":("著","˙ㄓㄜ"),
        "新的":("的","˙ㄉㄜ"),"說得好":("得","˙ㄉㄜ"),
        "桌子":("子","˙ㄗ"),"椅子":("子","˙ㄗ"),"蚊子":("子","˙ㄗ"),"李子":("子","˙ㄗ"),
        "你們":("們","˙ㄇㄣ"),"我們":("們","˙ㄇㄣ"),"他們":("們","˙ㄇㄣ"),"一個人":("個","˙ㄍㄜ"),
        "下巴":("巴","˙ㄅㄚ"),"尾巴":("巴","˙ㄅㄚ"),"嘴巴":("巴","˙ㄅㄚ"),
        "蘿蔔":("蔔","˙ㄅㄛ"),"姑娘":("娘","˙ㄋㄧㄤ"),"暖和":("和","˙ㄏㄨㄛ"),"意思":("思","˙ㄙ"),"耳朵":("朵","˙ㄉㄨㄛ"),
        # 本調區
        "先生":("生","ㄕㄥ"),"地道":("道","ㄉㄠˋ"),"告訴":("訴","ㄙㄨˋ"),"四海":("海","ㄏㄞˇ"),"兄弟":("弟","ㄉㄧˋ"),"人家":("家","ㄐㄧㄚ"),"下水":("水","ㄕㄨㄟˇ"),"故事":("事","ㄕˋ"),"名字":("字","ㄗˋ"),"客氣":("氣","ㄑㄧˋ"),"沒關係":("係","ㄒㄧˋ"),"犄角":("角","ㄐㄧㄠˇ"),
        "回來":("來","ㄌㄞˊ"),"下來":("來","ㄌㄞˊ"),"回去":("去","ㄑㄩˋ"),"下去":("去","ㄑㄩˋ"),
        "泥巴":("巴","ㄅㄚ"),"結巴":("巴","ㄅㄚ"),"啞巴":("巴","ㄅㄚ"),
        "遛達":("達","˙ㄉㄚ"),"疙瘩":("瘩","ㄉㄚ"),"葡萄":("萄","ㄊㄠˊ"),"玻璃":("璃","ㄌㄧˊ"),"枇杷":("杷","ㄆㄚˊ"),"茉莉":("莉","ㄌㄧˋ"),"玫瑰":("瑰","ㄍㄨㄟ"),"囉唆":("唆","ㄙㄨㄛ"),"餛飩":("飩","ㄉㄨㄣˋ"),"葫蘆":("蘆","ㄌㄨˊ"),"喇叭":("叭","ㄅㄚ"),
        "衣服":("服","ㄈㄨˊ"),"眉毛":("毛","ㄇㄠˊ"),"指甲":("甲","ㄐㄧㄚˇ"),"腦袋":("袋","ㄉㄞˋ"),"豆腐":("腐","ㄈㄨˇ"),"巴結":("結","ㄐㄧㄝˊ"),"蒼蠅":("蠅","ㄧㄥˊ"),"狐狸":("狸","ㄌㄧˊ"),"裁縫":("縫","ㄈㄥˊ"),"打發":("發","ㄈㄚ"),"秀氣":("氣","ㄑㄧˋ"),"芝麻":("麻","ㄇㄚˊ"),"棉花":("花","ㄏㄨㄚ"),"熱鬧":("鬧","ㄋㄠˋ"),"莊稼":("稼","ㄐㄧㄚˋ"),"迷糊":("糊","ㄏㄨˊ"),"商量":("量","ㄌㄧㄤˊ"),"俏皮":("皮","ㄆㄧˊ"),"機伶":("伶","ㄌㄧㄥˊ"),"破費":("費","ㄈㄟˋ"),"折騰":("騰","ㄊㄥˊ"),"敢情":("情","ㄑㄧㄥˊ"),"窩囊":("囊","ㄋㄤˊ"),"苗條":("條","ㄊㄧㄠˊ"),"舒服":("服","ㄈㄨˊ"),"掃帚":("帚","ㄓㄡˇ"),"漂亮":("亮","ㄌㄧㄤˋ"),"大夫":("夫","ㄈㄨ"),"胳膊":("膊","ㄅㄛˊ"),
        "部分":("分","ㄈㄣˋ"),"個個":("個","ㄍㄜˋ"),
    }
    for i,(ph,(ch,rd)) in enumerate(phrase_rules.items(),1):
        add_rule(rules,f"HB-NOTE-{i:03d}",ch,ph,rd,sec,"手冊輕聲區／本調區直接列示。",priority=PRIORITY_DETAIL)
    # Source itself lists 額頭 in both light-tone and full-tone groups; preserve the ambiguity.
    add_rule(rules,"HB-NOTE-ETOU-1","頭","額頭","˙ㄊㄡ",sec,"手冊同頁亦另列額頭ㄊㄡˊ，故保留衝突供人工判斷。",priority=PRIORITY_DETAIL)
    add_rule(rules,"HB-NOTE-ETOU-2","頭","額頭","ㄊㄡˊ",sec,"手冊同頁亦另列額頭輕聲，故保留衝突供人工判斷。",priority=PRIORITY_DETAIL)
    # 東西 has a semantic split; preserve both so runtime abstains unless more specific context is added later.
    add_rule(rules,"HB-NOTE-DONGXI-1","西","東西","ㄒㄧ",sec,"指東邊與西邊。",priority=PRIORITY_DETAIL)
    add_rule(rules,"HB-NOTE-DONGXI-2","西","東西","˙ㄒㄧ",sec,"指物品。",priority=PRIORITY_DETAIL)
    # 一分子 contains two explicit syllable rules.
    add_rule(rules,"HB-NOTE-YIFENZI-FEN","分","一分子","ㄈㄣˋ",sec,"手冊本調區列示一分子（ㄈㄣˋ ㄗˇ）。",priority=PRIORITY_DETAIL)
    add_rule(rules,"HB-NOTE-YIFENZI-ZI","子","一分子","ㄗˇ",sec,"手冊本調區列示一分子（ㄈㄣˋ ㄗˇ）。",priority=PRIORITY_DETAIL)
    # 便宜 has both syllables printed.
    add_rule(rules,"HB-NOTE-PIANYI-BIAN","便","便宜","ㄆㄧㄢˊ",sec,"手冊本調區列示便宜（ㄆㄧㄢˊ ㄧˊ）。",priority=PRIORITY_DETAIL)
    add_rule(rules,"HB-NOTE-PIANYI-YI","宜","便宜","ㄧˊ",sec,"手冊本調區列示便宜（ㄆㄧㄢˊ ㄧˊ）。",priority=PRIORITY_DETAIL)
    # 骨頭 explicit first and second syllables.
    add_rule(rules,"HB-NOTE-GUTOU-GU","骨","骨頭","ㄍㄨˊ",sec,"手冊列骨（ㄍㄨˊ）頭（輕聲）。",priority=PRIORITY_DETAIL)
    add_rule(rules,"HB-NOTE-GUTOU-TOU","頭","骨頭","˙ㄊㄡ",sec,"手冊列骨（ㄍㄨˊ）頭（輕聲）。",priority=PRIORITY_DETAIL)


def dedupe_rules(rows: list[dict]) -> list[dict]:
    # Keep identical evidence once, but preserve conflicting readings from the source.
    seen=set(); out=[]
    for r in rows:
        key=(r["target_char"],r["phrase"],r["expected_reading"],r["mode"],r["priority"])
        if key in seen:
            continue
        seen.add(key); out.append(r)
    return out


def main():
    ap=argparse.ArgumentParser(description="從《統一用字手冊》抽取可安全機讀的注音規則")
    ap.add_argument("docx", type=Path)
    ap.add_argument("--rules", type=Path, default=Path(__file__).with_name("統一用字手冊_注音規則.csv"))
    ap.add_argument("--constraints", type=Path, default=Path(__file__).with_name("統一用字手冊_字音限制.csv"))
    args=ap.parse_args()
    doc=Document(args.docx)
    rules=[]
    import_table_4_5_6(doc,rules)
    import_narrative_examples(rules)
    import_detailed_sections(rules)
    import_light_tone_note(rules)
    rules=dedupe_rules(rules)
    constraints=import_constraints_table10(doc)
    # de-duplicate constraints by char; later identical manual entry replaces earlier only when same set.
    cdict={}
    for c in constraints:
        cdict[c['char']]=c
    constraints=list(cdict.values())
    with args.rules.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=RULE_FIELDS); w.writeheader(); w.writerows(rules)
    with args.constraints.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=CONSTRAINT_FIELDS); w.writeheader(); w.writerows(constraints)
    print(f"rules={len(rules)} constraints={len(constraints)}")

if __name__=='__main__':
    main()
