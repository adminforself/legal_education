#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
湖南如法网学习自动化脚本
功能：自动登录、获取课程、完成答题
日期：2025-09-11
"""

import requests
import json
from bs4 import BeautifulSoup
import re
import itertools
import time
import redis
import logging
import base64
import urllib
import sys
from typing import Dict, List, Optional, Tuple, Any


# 配置彩色日志
class ColoredFormatter(logging.Formatter):
    """自定义日志格式化器，支持颜色输出"""

    # 颜色代码
    COLORS = {
        'DEBUG': '\033[0;36m',  # 青色
        'INFO': '\033[0;32m',  # 绿色
        'WARNING': '\033[1;33m',  # 黄色
        'ERROR': '\033[1;31m',  # 红色
        'CRITICAL': '\033[1;41m',  # 红色背景
        'RESET': '\033[0m'  # 重置颜色
    }

    def format(self, record):
        # 添加颜色到日志级别
        levelname = record.levelname
        if levelname in self.COLORS:
            record.levelname = f"{self.COLORS[levelname]}{levelname}{self.COLORS['RESET']}"

        # 调用父类格式化方法
        result = super().format(record)

        # 在日志之间添加空行
        if not hasattr(self, '_last_level'):
            self._last_level = None

        if self._last_level != record.levelname:
            result = f"\n{result}"
            self._last_level = record.levelname

        return result


# 配置日志
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 创建控制台处理器
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# 设置日志格式
formatter = ColoredFormatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
console_handler.setFormatter(formatter)

# 添加处理器到日志器
logger.addHandler(console_handler)

# 百度OCR文字识别配置
# 获取界面：https://console.bce.baidu.com/ai-engine/old/#/ai/ocr/app/list
CLIENT_ID = '您的API_KEY'  # 第一个输入API_KEY
CLIENT_SECRET = '您的Secret_KEY'  # 第二个输入Secret_KEY


# 获取用户凭证
def get_credentials() -> Tuple[str, str]:
    """获取用户名和密码"""
    try:
        username = input("请输入手机号：\n").strip()
        password = input("请输入密码：\n").strip()

        if not username or not password:
            logger.error("❌ 用户名或密码不能为空")
            sys.exit(1)

        return username, password
    except KeyboardInterrupt:
        logger.info("\n👋 用户中断操作，程序退出")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ 获取用户凭证时发生错误: {e}")
        sys.exit(1)


USERNAME, PASSWORD = get_credentials()


# 初始化Redis连接
def init_redis() -> Optional[redis.Redis]:
    """初始化Redis连接"""
    try:
        redis_client = redis.Redis(
            host='localhost',
            port=6379,
            db=0,
            socket_timeout=5,
            socket_connect_timeout=5,
            decode_responses=True  # 自动解码返回的字节数据
        )
        redis_client.ping()  # 测试连接
        logger.info("🎯 Redis连接成功，准备使用缓存功能")
        return redis_client
    except redis.ConnectionError:
        logger.warning("⚠️ 无法连接到Redis，将不使用缓存功能")
        return None
    except Exception as e:
        logger.warning(f"⚠️ Redis连接异常: {e}，将不使用缓存功能")
        return None


REDIS_CLIENT = init_redis()

# 设置请求头
HEADERS = {
    'Pragma': 'no-cache',
    'X-Requested-With': 'XMLHttpRequest',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
    'Content-Type': 'application/json; charset=UTF-8',
    'Accept': '*/*',
    'Host': 'hn.12348.gov.cn',
    'Connection': 'keep-alive'
}

# 有趣的日志消息
FUN_MESSAGES = {
    "start": "🚀 开始获取必修课程，准备起飞啦！",
    "fetch_success": "✅ 成功获取到 {} 门课程，学习之路开启！",
    "fetch_fail": "❌ 获取必修课程失败，可能是网络问题，请检查后重试",
    "book_detail": "📚 开始获取书籍详情，翻开知识的篇章",
    "book_success": "✅ 成功获取到 {} 个章节，准备开始学习！",
    "book_fail": "❌ 获取书籍详情失败，可能是服务器繁忙",
    "question_start": "🧠 开始获取问题，准备挑战你的知识极限",
    "question_success": "✅ 成功提取到 {} 道题目，准备好接受挑战了吗？",
    "question_fail": "❌ 获取题目失败，可能是页面结构发生了变化",
    "redis_hit": "🎯 从缓存中找到了题目 {} 的答案: {}",
    "redis_miss": "🤔 缓存中没有题目 {} 的答案，需要探索新知识",
    "answer_correct": "🎉 太棒了！题目 {} 回答正确！正确答案是: {}",
    "answer_wrong": "😅 答案 {} 不太对，再试试别的",
    "answer_submit": "📤 提交答案: {}",
    "answer_complete": "🏆 答题完成！总共答对了 {} 道题，你真是学霸！",
    "waiting": "⏳ 等待2秒，避免请求过快",
    "login_success": "🔑 登录成功！",
    "login_fail": "❌ 登录失败，请检查用户名、密码和验证码",
    "verification_fail": "❌ 验证码识别失败，尝试重新获取",
    "network_error": "🌐 网络请求异常，请检查网络连接"
}


# 请求重试装饰器
def retry_request(max_retries=3, delay=2):
    """请求重试装饰器"""

    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
                    if attempt == max_retries - 1:
                        logger.error(f"❌ 请求失败，已达最大重试次数: {e}")
                        raise
                    logger.warning(f"⚠️ 请求失败，{delay}秒后重试 ({attempt + 1}/{max_retries}): {e}")
                    time.sleep(delay)

        return wrapper

    return decorator


@retry_request(max_retries=3, delay=2)
def safe_request(method: str, url: str, **kwargs) -> Optional[requests.Response]:
    """安全的网络请求函数，包含异常处理和重试机制"""
    try:
        response = requests.request(method, url, **kwargs, timeout=30)
        response.raise_for_status()
        return response
    except requests.exceptions.Timeout:
        logger.error("⏰ 请求超时，请检查网络连接")
        raise
    except requests.exceptions.ConnectionError:
        logger.error("🔌 连接错误，请检查网络连接")
        raise
    except requests.exceptions.HTTPError as e:
        logger.error(f"🌐 HTTP错误: {e}")
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"🌐 网络请求异常: {e}")
        raise


def get_baidu_ocr_token() -> str:
    """
    百度OCR获取鉴权token
    文档：https://cloud.baidu.com/doc/OCR/s/Ck3h7y2ia
    """
    try:
        host = f'https://aip.baidubce.com/oauth/2.0/token?grant_type=client_credentials&client_id={CLIENT_ID}&client_secret={CLIENT_SECRET}'
        request = urllib.request.Request(host)
        request.add_header('Content-Type', 'application/json; charset=UTF-8')
        response = urllib.request.urlopen(request)
        token_content = response.read()

        if token_content:
            token_info = json.loads(token_content)
            return token_info['access_token']
    except Exception as e:
        logger.error(f"❌ 获取百度OCR token失败: {e}")
        raise


def image_to_word(image_data: str) -> str:
    """
    调用百度OCR，自动识别验证码
    每个账号每月1000次的识别额度
    文档：https://cloud.baidu.com/doc/OCR/s/1k3h7y3db
    """
    try:
        access_token = get_baidu_ocr_token()
        data = {'image': image_data}
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}

        url = f"https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic?access_token={access_token}"
        response = requests.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()

        result = response.json()
        code = result['words_result'][0]['words']
        logger.info(f"📷 识别的验证码: {code}")
        return code
    except Exception as e:
        logger.error(f"❌ OCR识别失败: {e}")
        raise


def get_verification_code() -> Dict[str, str]:
    """获取如法网的验证码"""
    try:
        url = "http://hn.12348.gov.cn/ucenter/api/kaptcha"
        headers = {
            'User-Agent': 'Apifox/1.0.0 (https://apifox.com)',
            'Accept': '*/*',
            'Host': 'hn.12348.gov.cn',
            'Connection': 'keep-alive'
        }

        response = safe_request('GET', url, headers=headers)
        response_data = response.json()

        captchaKey = response_data['body']['captchaKey']
        image = response_data['body']['base64Img']

        return {"captchaKey": captchaKey, "image": image}
    except Exception as e:
        logger.error(f"❌ 获取验证码失败: {e}")
        raise


def get_login_data(verification_data: Dict[str, str]) -> Dict[str, str]:
    """处理登录数据，包括验证码识别"""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            code = image_to_word(verification_data['image'])
            if len(code) == 4:  # 验证码应该是4位
                verification_data["code"] = code
                return verification_data

            logger.warning(f"⚠️ 验证码识别结果长度不正确: {code} (长度: {len(code)})")
            if attempt < max_attempts - 1:
                logger.info("🔄 重新获取验证码...")
                verification_data = get_verification_code()
                time.sleep(1)
        except Exception as e:
            logger.error(f"❌ 处理登录数据失败: {e}")
            if attempt == max_attempts - 1:
                raise

    logger.error(FUN_MESSAGES["verification_fail"])
    raise ValueError("验证码识别失败")


def do_login(login_data: Dict[str, str]) -> str:
    """用户登录逻辑"""
    try:
        login_header = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) ',
            'Content-Type': 'application/json;charset=UTF-8',
            'Accept': '*/*',
            'Referer': 'http://hn.12348.gov.cn/ucenter/',
            'Connection': 'keep-alive'
        }

        url = "http://hn.12348.gov.cn/ucenter/api/doLogin"
        encoded_password = base64.b64encode(PASSWORD.encode('utf-8')).decode('utf-8')

        payload = {
            "redirectUrl": "http://hn.12348.gov.cn/fxmain/study?biz=fxmain",
            "username": USERNAME,
            "password": encoded_password,
            "code": int(login_data['code']),
            "captchaKey": login_data['captchaKey'],
            "ifRemember": False
        }

        response = safe_request('POST', url, headers=login_header, data=json.dumps(payload))
        response_data = response.json()

        if response_data.get("success"):
            logger.info(FUN_MESSAGES["login_success"])
            return f"_tf_sso_main_session_id={response_data['body']}"
        else:
            logger.error(FUN_MESSAGES["login_fail"])
            raise ValueError(f"登录失败: {response_data.get('msg', '未知错误')}")
    except Exception as e:
        logger.error(f"❌ 登录过程失败: {e}")
        raise


def fetch_legal_publicity() -> Optional[List[Dict[str, Any]]]:
    """获取必修的所有课程"""
    logger.info(FUN_MESSAGES["start"])

    url = "http://hn.12348.gov.cn/fxmain/legalpublicity/queryPubBook"
    payload = {"condition": {"contentType1": 2, "state": 3, "businessCode": 4}}

    try:
        response = safe_request('POST', url, headers=HEADERS, json=payload)
        data = response.json()

        # 只获取前两门课程，避免过多请求
        result = [{"title": item["title"], "id": item["id"]} for item in data[:2]]
        logger.info(FUN_MESSAGES["fetch_success"].format(len(result)))

        return result
    except Exception as e:
        logger.error(FUN_MESSAGES["fetch_fail"])
        logger.debug(f"错误详情: {e}")
        return None


def get_book_detail(books: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """获取书籍详情"""
    logger.info(FUN_MESSAGES["book_detail"])

    url = "http://hn.12348.gov.cn/fxmain/legalpublicity/querycontentall"
    results = []

    for book in books:
        try:
            payload = {"contentId": book.get("id")}
            response = safe_request('POST', url, headers=HEADERS, json=payload)
            response_data = response.json()

            for item in response_data:
                results.append({
                    "title": item.get("title", "未知标题"),
                    "chapId": item.get("id"),
                    "caseId": book.get("id")
                })
        except Exception as e:
            logger.error(f"❌ 获取书籍 {book.get('title', '未知')} 详情失败: {e}")
            continue

    if results:
        logger.info(FUN_MESSAGES["book_success"].format(len(results)))
    else:
        logger.warning(FUN_MESSAGES["book_fail"])

    return results


def get_questions(result: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[BeautifulSoup]]:
    """获取问题"""
    logger.info(f"📖 处理章节: {result.get('title', '未知章节')}")

    url = f"http://hn.12348.gov.cn/fxmain/onlineanswer/os?caseId={result.get('caseId')}&chapId={result.get('chapId')}"

    try:
        response = safe_request('GET', url, headers=HEADERS)
        html_content = response.text
        soup = BeautifulSoup(html_content, 'html.parser')

        questions = soup.find_all('span', class_=re.compile('question'))
        question_data = []

        for question in questions:
            qid = question.get('qid')
            flag = question.get('flag')

            if qid and flag:
                question_data.append({'qid': qid, 'flag': flag})

        if question_data:
            logger.info(FUN_MESSAGES["question_success"].format(len(question_data)))
        else:
            logger.warning("📝 这个章节没有找到题目")

        return question_data, soup
    except Exception as e:
        logger.error(FUN_MESSAGES["question_fail"])
        logger.debug(f"错误详情: {e}")
        return [], None


def submit_answer(qid: str, caseId: str, chapId: str, flag: str,
                  answer_result: str, correct_answers: Dict[str, str],
                  from_cache: bool) -> bool:
    """提交答案并检查结果"""
    url = "http://hn.12348.gov.cn/fxmain/onlineanswer/ex"

    payload_dict = {
        "questionId": qid,
        "contentId": caseId,
        "contentType": "2",
        "flag": flag,
        "answerResult": answer_result,
        "chapterId": chapId
    }

    try:
        payload_str = json.dumps([payload_dict])

        if from_cache:
            logger.info(f"🔄 {FUN_MESSAGES['answer_submit'].format(answer_result)} (来自缓存)")
        else:
            logger.info(f"🔍 {FUN_MESSAGES['answer_submit'].format(answer_result)}")

        response = safe_request('POST', url, headers=HEADERS, data=payload_str)
        response_data = response.json()

        if response_data.get('code') == 200:
            result = response_data.get('extend', {}).get('result', {})
            if result.get(qid) == '1':
                logger.info(FUN_MESSAGES["answer_correct"].format(qid, answer_result))
                correct_answers[qid] = answer_result

                # 如果不是来自缓存，存储到Redis
                if not from_cache and REDIS_CLIENT:
                    try:
                        REDIS_CLIENT.set(f"answer:{qid}", answer_result, ex=86400)  # 缓存24小时
                        logger.info(f"💾 已将题目 {qid} 的正确答案存入缓存")
                    except Exception as e:
                        logger.warning(f"⚠️ 存储到缓存错误: {e}")

                # 无论是否成功，都等待2秒
                logger.info(FUN_MESSAGES["waiting"])
                time.sleep(2)
                return True
            else:
                logger.info(FUN_MESSAGES["answer_wrong"].format(answer_result))
        else:
            logger.error(f"❌ 请求失败: {response_data.get('msg')}")

    except Exception as e:
        logger.error(f"❌ 提交答案异常: {e}")

    # 无论是否成功，都等待2秒
    logger.info(FUN_MESSAGES["waiting"])
    time.sleep(2)
    return False


def to_answer(question_data: List[Dict[str, Any]], caseId: str,
              chapId: str, soup: BeautifulSoup) -> Dict[str, str]:
    """答题函数"""
    correct_answers = {}

    for question in question_data:
        qid = question['qid']
        flag = question['flag']
        cached_answer = None

        # 检查缓存中是否已有正确答案
        if REDIS_CLIENT:
            try:
                cached_answer = REDIS_CLIENT.get(f"answer:{qid}")
                if cached_answer:
                    logger.info(FUN_MESSAGES["redis_hit"].format(qid, cached_answer))

                    # 即使从缓存获取答案，也需要提交
                    if submit_answer(qid, caseId, chapId, flag, cached_answer, correct_answers, True):
                        continue
                    else:
                        # 如果提交失败，删除缓存并继续尝试
                        logger.warning("⚠️ 缓存答案提交失败，删除缓存并重新尝试")
                        REDIS_CLIENT.delete(f"answer:{qid}")
                        cached_answer = None
            except Exception as e:
                logger.warning(f"⚠️ 缓存操作错误: {e}")

        if not cached_answer:
            logger.info(FUN_MESSAGES["redis_miss"].format(qid))

        # 查找该题目的所有选项
        options = soup.find_all('input', {'name': qid})
        all_options = [option.get('value') for option in options if option.get('value')]

        if not all_options:
            logger.warning(f"⚠️ 题目 {qid} 没有找到选项")
            continue

        logger.info(f"📋 题目 {qid} 的所有选项: {all_options}")

        # 尝试所有可能的答案组合
        if flag == '1':  # 单选题
            for option in all_options:
                if submit_answer(qid, caseId, chapId, flag, option, correct_answers, False):
                    break

        elif flag == '2':  # 多选题
            # 生成所有可能的选项组合（从2个选项开始）
            min_options = max(2, 1)  # 多选题至少选择2个选项
            max_options = len(all_options)

            # 尝试不同长度的组合
            for r in range(min_options, max_options + 1):
                combinations = list(itertools.combinations(all_options, r))

                for combination in combinations:
                    answer_result = ','.join(combination)
                    if submit_answer(qid, caseId, chapId, flag, answer_result, correct_answers, False):
                        break
                else:
                    continue  # 继续下一个r值
                break  # 如果找到了正确答案，跳出外层循环

    logger.info(FUN_MESSAGES["answer_complete"].format(len(correct_answers)))
    return correct_answers


def main():
    """主函数"""
    try:
        # 1. 登录前先获取验证码
        verification_data = get_verification_code()

        # 2. 获取到验证图片后，进行文字识别，最多识别3次
        login_data = get_login_data(verification_data)

        # 3. 登录成功获取token
        cookie = do_login(login_data)

        # 4. 更新headers头部
        HEADERS['Cookie'] = cookie
        logger.info(f"🍪 登录成功，Cookie已更新")

        time.sleep(2)

        # 5. 获取必修课程
        legal_data = fetch_legal_publicity()
        if not legal_data:
            logger.error("❌ 无法获取必修课程，程序退出")
            return

        # 6. 获取书籍详情
        results = get_book_detail(legal_data)
        if not results:
            logger.error("❌ 无法获取书籍详情，程序退出")
            return

        # 7. 处理每个章节的问题
        for result in results:
            question_data, soup = get_questions(result)

            if question_data and soup:
                to_answer(question_data, result['caseId'], result['chapId'], soup)
            else:
                logger.warning(f"📝 章节 {result.get('title', '未知')} 没有题目或获取失败")

    except KeyboardInterrupt:
        logger.info("\n👋 用户中断操作，程序退出")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ 程序执行异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

