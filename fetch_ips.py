#!/usr/bin/env python
# -*- coding:utf-8 -*-
#   
#   Author  :   XueWeiHan
#   E-mail  :   595666367@qq.com
#   Date    :   2020-05-19 15:27
#   Desc    :   获取最新的 GitHub 相关域名对应 IP
import re
from typing import Any, Dict, List, Optional
from datetime import datetime
import sys
import asyncio
import aiodns
import pycares

from pythonping import ping
from requests_html import HTMLSession
from retry import retry

from common import GITHUB_URLS, write_hosts_content


PING_TIMEOUT_SEC: int = 1
DISCARD_LIST: List[str] = ["1.0.1.1", "1.2.1.1", "127.0.0.1"]


PING_LIST: Dict[str, int] = dict()


def ping_cached(ip: str) -> int:
    """
    带缓存的 ping 测速，返回指定 IP 的延迟中位数。

    对同一 IP 仅执行一次实际测速（连续 3 次 ping），
    后续调用直接从缓存字典 PING_LIST 返回已有结果，避免重复测速。

    Args:
        ip: 待测速的目标 IPv4 地址

    Returns:
        三次 ping 的中位数延迟（毫秒），已缓存则直接返回缓存值
    """
    global PING_LIST
    if ip in PING_LIST:
        return PING_LIST[ip]
    ping_times = [ping(ip, timeout=PING_TIMEOUT_SEC).rtt_avg_ms for _ in range(3)]
    ping_times.sort()
    print(f'Ping {ip}: {ping_times} ms')
    PING_LIST[ip] = ping_times[1] # 取中位数
    return PING_LIST[ip]


def select_ip_from_list(ip_list: List[str]) -> Optional[str]:
    """
    从 IP 列表中通过 ping 测速选出延迟最低的最优 IP。

    对列表中每个 IP 执行缓存的 ping 测速，按延迟升序排列后
    返回延迟最小的 IP；列表为空时返回 None。

    Args:
        ip_list: 候选 IPv4 地址列表

    Returns:
        延迟最低的 IP 地址；若列表为空则返回 None
    """
    if len(ip_list) == 0:
        return None
    ping_results = [(ip, ping_cached(ip)) for ip in ip_list]
    ping_results.sort(key=lambda x: x[1])
    best_ip = ping_results[0][0]
    print(f"{ping_results}, selected {best_ip}")
    return best_ip


#致命问题：无法绕过Cloudflare验证，造成算力浪费
@retry(tries=3)
def get_ip_list_from_ipaddress_com(session: Any, github_url: str) -> Optional[List[str]]:
    """
    从 ipaddress.com 网站抓取指定 GitHub 域名对应的 IP 地址列表。

    通过解析 ipaddress.com 的域名查询页面，提取页面中出现的所有
    IPv4 地址。失败时自动重试最多 3 次。

    Args:
        session: requests_html.HTMLSession 实例，用于发起 HTTP 请求
        github_url: GitHub 域名，如 "github.com"

    Returns:
        提取到的 IPv4 地址列表；若请求或解析失败则抛出异常
    """
    url = f'https://sites.ipaddress.com/{github_url}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
                      ' AppleWebKit/537.36 (KHTML, like Gecko) Chrome/1'
                      '06.0.0.0 Safari/537.36'}
    try:
        rs = session.get(url, headers=headers, timeout=5)
        pattern = r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"
        ip_list = re.findall(pattern, rs.html.text)
        # print(f'ipaddress_com:{ip_list}')
        return ip_list
    except Exception as ex:
        print(f"get: {url}, error: {ex}")
        raise Exception


DNS_SERVER_LIST = [
    "1.1.1.1",  # Cloudflare
    "8.8.8.8",  # Google
    "101.101.101.101",  # Quad101
    "101.102.103.104",  # Quad101
]


def windows_compatibility_check():
    if sys.platform == "win32":
        # 检查 pycares 是否正常加载
        try:
            import pycares
        except ImportError:
            raise RuntimeError("请先执行 'pip install pycares'")


async def get_ip_list_from_dns(
    domain,
    record_type="A",
    dns_server_list=["1.2.4.8", "114.114.114.114"],
):
    # Windows 兼容性检查
    windows_compatibility_check()

    # 配置 DNS 服务器
    resolver = aiodns.DNSResolver()
    resolver.nameservers = dns_server_list

    try:
        # 执行异步查询
        result = await resolver.query_dns(domain, record_type)
        ip_list = [record.data.addr for record in result.answer if record.type == pycares.QUERY_TYPE_A]
        return ip_list
    except aiodns.error.DNSError as e:
        print(f"{domain}: DNS 查询失败: {e}")
        return []


async def get_ip(session: Any, github_url: str, signal: int = 0) -> Optional[dict[str,str]]:
    """
       综合网页抓取和 DNS 查询两种方式，获取指定 GitHub 域名的最优 IP。

       分别从 ipaddress.com 抓取和 DNS 解析获取候选 IP，
       去重、排除已知无效地址后通过 ping 测速选出延迟最低的 IP。

       Args:
           session: requests_html.HTMLSession 实例，用于网页抓取
           github_url: GitHub 域名，如 "github.com"

       Returns:
           ping 延迟最低的 IP 地址；若两种方式均未获取到 IP 则返回 None
       """
    print(f"{signal} - {github_url} start")
    ip_list_web = []
    # get_ip_list_from_ipaddress_com暂时停用
    # try:
    #     ip_list_web = get_ip_list_from_ipaddress_com(session, github_url)
    # except Exception as ex:
    #     pass
    ip_list_dns = []
    try:
        ip_list_dns = await get_ip_list_from_dns(github_url, dns_server_list=DNS_SERVER_LIST)
    except Exception as ex:
        pass
    ip_list_set = set(ip_list_web + ip_list_dns)
    for discard_ip in DISCARD_LIST:
        ip_list_set.discard(discard_ip)
    ip_list = list(ip_list_set)
    ip_list.sort()
    if len(ip_list) == 0:
        return None
    print(f"{github_url}: {ip_list}")
    best_ip = select_ip_from_list(ip_list)
    print(f"{signal} - {github_url} end")
    return {github_url: best_ip}


async def main() -> None:
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'{current_time} - Start script.')
    session = HTMLSession()
    content = ""
    content_list = []
    print('Start Select IP:')
    gather_task = [get_ip(session, github_url, index + 1) for index, github_url in enumerate(GITHUB_URLS)]
    task = await asyncio.gather(*gather_task)
    task_dict = dict()
    for dict_item in task:
        if dict_item is not None:
            task_dict |= dict_item
    for index, github_url in enumerate(task_dict):
        ip = task_dict[github_url]
        print(f'Start Processing url: {index + 1}/{len(GITHUB_URLS)}, {github_url}')
        try:
            if ip is None:
                print(f"{github_url}: IP Not Found")
                ip = "# IP Address Not Found"
            content += ip.ljust(30) + github_url
            global PING_LIST
            if PING_LIST.get(ip) is not None and PING_LIST.get(ip) == PING_TIMEOUT_SEC * 1000:
                content += "  # Timeout"
            content += "\n"
            content_list.append((ip, github_url,))
        except Exception:
            continue

    write_hosts_content(content, content_list)
    # print(hosts_content)
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'{current_time} - End script.')


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()