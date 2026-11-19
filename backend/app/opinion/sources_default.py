"""Starting list of opinion sources: regulators, the industry association and state media.

Data, not logic: a source is a publisher plus the section pages to read. The publisher host must be
on the source lock, so a source cannot be added that has not been confirmed as official.
(kind: regulator = government body, industry = industry association, media = state media.)
"""

DEFAULT_SOURCES = [
    ("国家互联网信息办公室", "regulator", "https://www.cac.gov.cn/"),
    ("工业和信息化部", "regulator", "https://www.miit.gov.cn/xwfb/index.html"),
    ("国家市场监督管理总局", "regulator", "https://www.samr.gov.cn/"),
    ("国家标准化管理委员会", "regulator", "https://www.sac.gov.cn/"),
    ("交通运输部", "regulator", "https://www.mot.gov.cn/"),
    ("中国政府网", "regulator", "https://www.gov.cn/zhengce/index.htm"),
    ("中国汽车工业协会", "industry", "http://www.caam.org.cn/"),
    ("新华网", "media", "https://www.news.cn/auto/"),
    ("人民网", "media", "http://auto.people.com.cn/"),
]

# 网信办首页导航里与汽车数据出境最相关、且 robots.txt 未禁止的栏目；
# 执法类栏目（/zfz/、/wxzf/）网站明确禁止机器人抓取，不读
DEFAULT_SOURCES += [
    ("国家互联网信息办公室", "regulator", "https://www.cac.gov.cn/wxzw/sjzl/A093708index_1.htm"),
    (
        "国家互联网信息办公室",
        "regulator",
        "https://www.cac.gov.cn/wxzw/sjzl/sjcjaqpg/A09370801index_1.htm",
    ),
]
