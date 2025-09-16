"""
模块名称: validators.py
功能描述: 数据验证器，用于验证道馆JSON、用户输入等
作者: @Kilo Code
创建日期: 2024-09-15
最后修改: 2024-09-15
"""

from typing import Dict, Any, Optional, List, Tuple
import re
from urllib.parse import urlparse


# Discord限制常量
EMBED_DESC_LIMIT = 4096
EMBED_FIELD_VALUE_LIMIT = 1024
EMBED_FIELD_NAME_LIMIT = 256
EMBED_TOTAL_LIMIT = 6000
BUTTON_LABEL_LIMIT = 80
MESSAGE_CONTENT_LIMIT = 2000


def validate_gym_json(data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    验证道馆JSON数据的结构和内容
    
    Args:
        data: 道馆JSON数据
    
    Returns:
        (是否有效, 错误消息)
    """
    # 检查必需的顶层键
    required_keys = ['id', 'name', 'description', 'tutorial', 'questions']
    missing_keys = [key for key in required_keys if key not in data]
    if missing_keys:
        return False, f"JSON数据缺少必需键: {', '.join(missing_keys)}"
    
    # 验证ID格式
    if not validate_gym_id(data['id']):
        return False, f"道馆ID格式无效: {data['id']}。ID应只包含字母、数字、下划线和连字符。"
    
    # 验证名称长度
    if len(data['name']) > 100:
        return False, "道馆名称不能超过100个字符"
    
    # 验证描述长度
    if len(data['description']) > 500:
        return False, "道馆描述不能超过500个字符"
    
    # 验证教程
    if not isinstance(data['tutorial'], list):
        return False, "`tutorial`字段必须是一个列表"
    
    tutorial_text = "\n".join(data['tutorial'])
    if len(tutorial_text) > EMBED_DESC_LIMIT:
        return False, f"`tutorial`的总长度超出了Discord {EMBED_DESC_LIMIT}字符的限制"
    
    # 验证题目列表
    if not isinstance(data['questions'], list):
        return False, "`questions`字段必须是一个列表"
    
    if len(data['questions']) == 0:
        return False, "道馆必须至少包含一个题目"
    
    # 验证可选的questions_to_ask
    if 'questions_to_ask' in data:
        if not isinstance(data['questions_to_ask'], int):
            return False, "`questions_to_ask`必须是一个整数"
        if data['questions_to_ask'] <= 0:
            return False, "`questions_to_ask`必须是大于0的整数"
        if data['questions_to_ask'] > len(data['questions']):
            return False, f"`questions_to_ask`({data['questions_to_ask']})不能超过题库中的总题目数({len(data['questions'])})"
    
    # 验证可选的allowed_mistakes
    if 'allowed_mistakes' in data:
        if not isinstance(data['allowed_mistakes'], int):
            return False, "`allowed_mistakes`必须是一个整数"
        if data['allowed_mistakes'] < 0:
            return False, "`allowed_mistakes`不能是负数"
    
    # 验证可选的badge_image_url
    if 'badge_image_url' in data and data['badge_image_url']:
        is_valid, error = validate_image_url(data['badge_image_url'])
        if not is_valid:
            return False, f"`badge_image_url`无效: {error}"
    
    # 验证可选的badge_description
    if 'badge_description' in data and data['badge_description']:
        if not isinstance(data['badge_description'], str):
            return False, "`badge_description`必须是一个字符串"
        if len(data['badge_description']) > EMBED_FIELD_VALUE_LIMIT:
            return False, f"`badge_description`的长度不能超过{EMBED_FIELD_VALUE_LIMIT}个字符"
    
    # 验证可选的randomize_options
    if 'randomize_options' in data:
        if not isinstance(data['randomize_options'], bool):
            return False, "`randomize_options`必须是一个布尔值(true或false)"
    
    # 验证每个题目
    for i, question in enumerate(data['questions'], 1):
        is_valid, error = validate_question(question, i)
        if not is_valid:
            return False, error
    
    return True, None


def validate_question(question: Dict[str, Any], question_num: int) -> Tuple[bool, Optional[str]]:
    """
    验证单个题目的结构
    
    Args:
        question: 题目数据
        question_num: 题目编号
    
    Returns:
        (是否有效, 错误消息)
    """
    if not isinstance(question, dict):
        return False, f"题目{question_num}不是一个有效的JSON对象"
    
    # 检查必需键
    required_keys = ['type', 'text', 'correct_answer']
    missing_keys = [key for key in required_keys if key not in question]
    if missing_keys:
        return False, f"题目{question_num}缺少必需键: {', '.join(missing_keys)}"
    
    # 验证题目文本长度
    if len(question['text']) > EMBED_DESC_LIMIT:
        return False, f"题目{question_num}的文本长度超出了Discord {EMBED_DESC_LIMIT}字符的限制"
    
    # 验证题目类型
    valid_types = ['multiple_choice', 'true_false', 'fill_in_blank']
    if question['type'] not in valid_types:
        return False, f"题目{question_num}的类型无效，必须是: {', '.join(valid_types)}"
    
    # 根据题目类型进行特定验证
    if question['type'] == 'multiple_choice':
        if 'options' not in question:
            return False, f"题目{question_num}(选择题)缺少`options`字段"
        if not isinstance(question['options'], list):
            return False, f"题目{question_num}的`options`必须是一个列表"
        if len(question['options']) < 2:
            return False, f"题目{question_num}(选择题)必须至少有2个选项"
        if len(question['options']) > 10:
            return False, f"题目{question_num}(选择题)的选项不能超过10个"
        if question['correct_answer'] not in question['options']:
            return False, f"题目{question_num}(选择题)的正确答案必须是选项之一"
    
    elif question['type'] == 'true_false':
        if question['correct_answer'] not in ['正确', '错误']:
            return False, f"题目{question_num}(判断题)的正确答案必须是'正确'或'错误'"
    
    elif question['type'] == 'fill_in_blank':
        # 填空题的correct_answer可以是字符串或列表
        if not isinstance(question['correct_answer'], (str, list)):
            return False, f"题目{question_num}(填空题)的正确答案必须是字符串或列表"
        if isinstance(question['correct_answer'], list):
            if len(question['correct_answer']) == 0:
                return False, f"题目{question_num}(填空题)的正确答案列表不能为空"
    
    return True, None


def validate_gym_id(gym_id: str) -> bool:
    """
    验证道馆ID格式
    
    Args:
        gym_id: 道馆ID
    
    Returns:
        是否有效
    """
    if not gym_id:
        return False
    # 允许字母、数字、下划线和连字符
    pattern = r'^[a-zA-Z0-9_-]+$'
    return bool(re.match(pattern, gym_id))


def validate_image_url(url: str) -> Tuple[bool, Optional[str]]:
    """
    验证图片URL
    
    Args:
        url: 图片URL
    
    Returns:
        (是否有效, 错误消息)
    """
    if not isinstance(url, str):
        return False, "URL必须是字符串"
    
    # 解析URL
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "无法解析URL"
    
    # 检查协议
    if parsed.scheme not in ['http', 'https']:
        return False, "URL必须使用http或https协议"
    
    # 检查文件扩展名
    valid_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.webp']
    if not any(url.lower().endswith(ext) for ext in valid_extensions):
        return False, f"URL必须是图片链接，支持的格式: {', '.join(valid_extensions)}"
    
    return True, None


def validate_discord_id(id_str: str) -> bool:
    """
    验证Discord ID (雪花ID)
    
    Args:
        id_str: ID字符串
    
    Returns:
        是否有效
    """
    if not id_str:
        return False
    # Discord ID是18-19位的数字
    return id_str.isdigit() and 17 <= len(id_str) <= 19


def validate_role_input(role_str: str) -> List[str]:
    """
    验证并解析身份组输入字符串
    
    Args:
        role_str: 身份组输入字符串(可以是ID或提及，逗号分隔)
    
    Returns:
        身份组ID列表
    
    Raises:
        ValueError: 如果输入无效
    """
    if not role_str:
        return []
    
    role_ids = []
    parts = [part.strip() for part in role_str.split(',')]
    
    for part in parts:
        if not part:
            continue
        
        # 检查是否是提及格式 <@&ROLE_ID>
        if part.startswith('<@&') and part.endswith('>'):
            role_id = part[3:-1]
        else:
            role_id = part
        
        if not validate_discord_id(role_id):
            raise ValueError(f"无效的身份组ID: {part}")
        
        role_ids.append(role_id)
    
    return role_ids


def validate_user_input(text: str, max_length: int = MESSAGE_CONTENT_LIMIT) -> Tuple[bool, Optional[str]]:
    """
    验证用户输入文本
    
    Args:
        text: 用户输入
        max_length: 最大长度
    
    Returns:
        (是否有效, 错误消息)
    """
    if not text:
        return False, "输入不能为空"
    
    if len(text) > max_length:
        return False, f"输入长度不能超过{max_length}个字符"
    
    # 检查是否包含Discord令牌模式(基本检查)
    token_pattern = r'[MN][A-Za-z\d]{23}\.[\w-]{6}\.[\w-]{27}'
    if re.search(token_pattern, text):
        return False, "输入包含敏感信息"
    
    return True, None


def validate_panel_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    验证挑战面板配置
    
    Args:
        config: 面板配置
    
    Returns:
        (是否有效, 错误消息)
    """
    # 验证完成阈值
    if 'completion_threshold' in config and config['completion_threshold'] is not None:
        if not isinstance(config['completion_threshold'], int):
            return False, "完成阈值必须是整数"
        if config['completion_threshold'] <= 0:
            return False, "完成阈值必须大于0"
    
    # 验证关联道馆
    if 'associated_gyms' in config and config['associated_gyms']:
        if not isinstance(config['associated_gyms'], list):
            return False, "关联道馆必须是列表"
        for gym_id in config['associated_gyms']:
            if not validate_gym_id(gym_id):
                return False, f"无效的道馆ID: {gym_id}"
    
    # 验证前置道馆
    if 'prerequisite_gyms' in config and config['prerequisite_gyms']:
        if not isinstance(config['prerequisite_gyms'], list):
            return False, "前置道馆必须是列表"
        for gym_id in config['prerequisite_gyms']:
            if not validate_gym_id(gym_id):
                return False, f"无效的前置道馆ID: {gym_id}"
    
    # 验证前置和关联道馆不能有交集
    if config.get('prerequisite_gyms') and config.get('associated_gyms'):
        intersection = set(config['prerequisite_gyms']) & set(config['associated_gyms'])
        if intersection:
            return False, f"道馆不能既是前置又是关联: {', '.join(intersection)}"
    
    return True, None


def validate_command_name(name: str) -> bool:
    """
    验证命令名称
    
    Args:
        name: 命令名称
    
    Returns:
        是否有效
    """
    if not name:
        return False
    # Discord命令名称规则：1-32个字符，只能包含小写字母、数字、连字符和下划线
    pattern = r'^[a-z0-9_-]{1,32}$'
    return bool(re.match(pattern, name.lower()))


def sanitize_filename(filename: str) -> str:
    """
    清理文件名，移除不安全字符
    
    Args:
        filename: 原始文件名
    
    Returns:
        清理后的文件名
    """
    # 移除路径分隔符和其他不安全字符
    unsafe_chars = ['/', '\\', '..', '~', '<', '>', ':', '"', '|', '?', '*']
    safe_name = filename
    for char in unsafe_chars:
        safe_name = safe_name.replace(char, '_')
    
    # 限制长度
    max_length = 255
    if len(safe_name) > max_length:
        # 保留扩展名
        parts = safe_name.rsplit('.', 1)
        if len(parts) == 2:
            name, ext = parts
            safe_name = name[:max_length - len(ext) - 1] + '.' + ext
        else:
            safe_name = safe_name[:max_length]
    
    return safe_name or 'unnamed'