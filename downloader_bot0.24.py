import os
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from yt_dlp import YoutubeDL
import instaloader
import aiohttp
from pyquery import PyQuery as pq
import requests
import json

# Константы
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_REQUESTS_PER_MINUTE = 5  # Ограничение на 5 запросов в минуту

# Словарь для отслеживания запросов
user_requests = {}

# Словарь для хранения данных
users_data = {}

# Файл для хранения данных
STATS_FILE = "user_stats.txt"

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Функция загрузки данных
def load_stats():
    global users_data
    try:
        with open(STATS_FILE, 'r') as file:
            for line in file:
                user_id, username, first_name, last_name, join_date = line.strip().split('|')
                users_data[int(user_id)] = {
                    'username': username,
                    'first_name': first_name,
                    'last_name': last_name,
                    'join_date': join_date
                }
    except FileNotFoundError:
        logging.info("Файл статистики не найден. Создаём новый.")

# Функция сохранения данных
def save_stats():
    with open("stats.txt", "a", encoding="utf-8") as file:
        file.write(f"{user_id}|{data['username']}|{data['first_name']}|{data['last_name']}|{data['join_date']}\n")

# Проверка размера файла
def check_file_size(file_path: str) -> bool:
    file_size = os.path.getsize(file_path)
    return file_size <= MAX_FILE_SIZE

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    if user_id not in users_data:
        join_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        users_data[user_id] = {
            'username': user.username or "None",
            'first_name': user.first_name or "None",
            'last_name': user.last_name or "None",
            'join_date': join_date
        }
        save_stats()
        logging.info(f"Новый пользователь: {user.full_name} ({user_id})")
    
    welcome_message = (
        "Hello! I'm a bot for downloading media from Instagram, YouTube, TikTok, Facebook, and Pinterest.\n"
        "Just send me a link to a post, video, or Stories.\n"
        "⚠️ Maximum file size for download: 50 MB."
    )
    await update.message.reply_text(welcome_message)

# Команда /stats
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_users = len(users_data)
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    new_users_count = sum(
        1 for data in users_data.values()
        if datetime.strptime(data['join_date'], '%Y-%m-%d %H:%M:%S') > yesterday
    )

    response = (
        f"📊 Bot Statistics:\n"
        f"👥 Total users: {total_users}\n"
        f"🚀 Active users (last 24h): {new_users_count}"
    )
    await update.message.reply_text(response)

# Обработчик сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    url = update.message.text
    output_path = f"media_{user_id}"

    if not os.path.exists(output_path):
        os.makedirs(output_path)

    # Проверка ограничения запросов
    now = datetime.now()
    if user_id in user_requests:
        user_requests[user_id] = [req for req in user_requests[user_id] if now - req < timedelta(minutes=1)]
        if len(user_requests[user_id]) >= MAX_REQUESTS_PER_MINUTE:
            await update.message.reply_text("You have exceeded the limit of 5 downloads per minute. Please wait.")
            return
        user_requests[user_id].append(now)
    else:
        user_requests[user_id] = [now]

    # Если ссылка сокращённая (pin.it), преобразуем её в полный URL
    if url.startswith("https://pin.it/"):
        expanded_url = expand_short_url(url)
        if expanded_url:
            url = expanded_url
        else:
            await update.message.reply_text("Failed to expand the short URL.")
            return

    try:
        if "instagram.com" in url:
            if "/stories/" in url:
                username = url.split("/stories/")[1].split("/")[0]
                result = download_instagram_stories(username, output_path)
                platform = "Instagram Stories"
            elif "/reel/" in url or "/p/" in url:
                result = download_instagram_media(url, output_path)
                platform = "Instagram"
            elif "/highlights/" in url:
                username = url.split("/highlights/")[1].split("/")[0]
                result = download_instagram_highlights(username, output_path)
                platform = "Instagram Highlights"
            else:
                result = "Unsupported Instagram link."
                platform = None
        elif "youtube.com" in url or "youtu.be" in url:
            result = download_youtube_video(url, output_path)
            platform = "YouTube"
        elif "tiktok.com" in url:
            result = download_tiktok_video(url, output_path)
            platform = "TikTok"
        elif "facebook.com" in url:
            result = download_facebook_video(url, output_path)
            platform = "Facebook"
        elif "pinterest.com" in url:
            download_url = await get_download_url(url)
            if download_url:
                if '.mp4' in download_url:
                    result = await download_video(download_url, output_path)
                    platform = "Pinterest Video"
                else:
                    result = await download_image(download_url, output_path)
                    platform = "Pinterest Image"
            else:
                result = "Failed to get download URL from Pinterest."
                platform = None
        else:
            result = "Unsupported platform. Please provide a valid link."
            platform = None

        if os.path.exists(result) and os.path.isfile(result):
            if check_file_size(result):
                with open(result, "rb") as file:
                    if result.endswith('.mp4'):
                        await update.message.reply_video(file)
                    else:
                        await update.message.reply_photo(file)
                os.remove(result)
            else:
                await update.message.reply_text("File size exceeds the limit of 50 MB.")
        else:
            await update.message.reply_text(result)

    except Exception as e:
        logging.error(f"Error processing message: {e}")
        await update.message.reply_text("An error occurred while processing your request. Please try again later.")

# Функция для скачивания медиа с Twitter
# Функция для скачивания медиа с X (Twitter)
def download_twitter_media(url: str, output_path: str) -> str:
    try:
        ydl_opts = {
            'format': 'best',  # Выбираем лучшее качество
            'outtmpl': f"{output_path}/%(title)s.%(ext)s",  # Шаблон имени файла
            'quiet': True,  # Отключаем вывод логов
        }
        with YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info_dict)
            return file_path
    except Exception as e:
        logging.error(f"Error downloading X (Twitter) media: {e}")
        return f"Error downloading X (Twitter) media: {e}"
        
# Функция для скачивания медиа с Instagram (Reels и посты)
def download_instagram_media(url: str, output_path: str) -> str:
    try:
        ydl_opts = {
            'format': 'best',
            'outtmpl': f"{output_path}/%(title)s.%(ext)s",
            'quiet': True,
        }
        with YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info_dict)
            return file_path
    except Exception as e:
        logging.error(f"Error downloading Instagram media: {e}")
        return f"Error downloading Instagram media: {e}"

# Функция для скачивания Stories с Instagram
def download_instagram_stories(username: str, output_path: str) -> str:
    L = instaloader.Instaloader()
    try:
        with open("auth.json", "r") as f:
            auth_data = json.load(f)
        L.login(auth_data["username"], auth_data["password"])
        profile = instaloader.Profile.from_username(L.context, username)
        for story in L.get_stories([profile.userid]):
            for item in story.get_items():
                L.download_storyitem(item, target=output_path)
        return output_path
    except Exception as e:
        logging.error(f"Error downloading Instagram Stories: {e}")
        return f"Error downloading Instagram Stories: {e}"

# Функция для скачивания Highlights с Instagram
def download_instagram_highlights(username: str, output_path: str) -> str:
    L = instaloader.Instaloader()
    try:
        with open("auth.json", "r") as f:
            auth_data = json.load(f)
        L.login(auth_data["username"], auth_data["password"])
        profile = instaloader.Profile.from_username(L.context, username)
        for highlight in L.get_highlights(profile):
            for item in highlight.get_items():
                L.download_storyitem(item, target=output_path)
        return output_path
    except Exception as e:
        logging.error(f"Error downloading Instagram Highlights: {e}")
        return f"Error downloading Instagram Highlights: {e}"

# Функция для скачивания видео с YouTube
def download_youtube_video(url: str, output_path: str) -> str:
    try:
        ydl_opts = {
            'format': 'best',
            'outtmpl': f"{output_path}/%(title)s.%(ext)s",
            'quiet': True,
        }
        with YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info_dict)
            return file_path
    except Exception as e:
        logging.error(f"Error downloading YouTube video: {e}")
        return f"Error downloading YouTube video: {e}"

# Функция для скачивания видео с TikTok
def download_tiktok_video(url: str, output_path: str) -> str:
    try:
        ydl_opts = {
    'format': 'best',
    'outtmpl': f"{output_path}/%(title)s.%(ext)s",
    'quiet': True,
    'headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
        'Referer': 'https://www.tiktok.com/',
    }
}
        with YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info_dict)
            return file_path
    except Exception as e:
        logging.error(f"Error downloading TikTok video: {e}")
        return f"Error downloading TikTok video: {e}"

# Функция для скачивания видео с Facebook
def download_facebook_video(url: str, output_path: str) -> str:
    try:
        ydl_opts = {
            'format': 'best',
            'outtmpl': f"{output_path}/%(title)s.%(ext)s",
            'quiet': True,
        }
        with YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info_dict)
            return file_path
    except Exception as e:
        logging.error(f"Error downloading Facebook video: {e}")
        return f"Error downloading Facebook video: {e}"

# Функция для преобразования сокращенных ссылок Pinterest
def expand_short_url(short_url: str) -> str:
    try:
        response = requests.get(short_url, allow_redirects=True)
        return response.url
    except Exception as e:
        logging.error(f"Error expanding short URL: {e}")
        return None

# Функция для получения ссылки на скачивание с Pinterest
async def get_download_url(link: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post('https://www.expertsphp.com/download.php', data={'url': link}) as response:
                content = await response.text()
                download_url = pq(content)('table.table-condensed')('tbody')('td')('a').attr('href')
                return download_url
    except Exception as e:
        logging.error(f"Error getting Pinterest download URL: {e}")
        return None

# Функция для скачивания видео
async def download_video(url: str, output_path: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    file_path = os.path.join(output_path, "pinterest_video.mp4")
                    with open(file_path, "wb") as file:
                        file.write(await response.read())
                    return file_path
                else:
                    return f"Failed to download video: {response.status}"
    except Exception as e:
        logging.error(f"Error downloading Pinterest video: {e}")
        return f"Error downloading Pinterest video: {e}"

# Функция для скачивания изображения
async def download_image(url: str, output_path: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    file_path = os.path.join(output_path, "pinterest_image.jpg")
                    with open(file_path, "wb") as file:
                        file.write(await response.read())
                    return file_path
                else:
                    return f"Failed to download image: {response.status}"
    except Exception as e:
        logging.error(f"Error downloading Pinterest image: {e}")
        return f"Error downloading Pinterest image: {e}"

# Основная функция
def main():
    load_stats()
    application = ApplicationBuilder().token("BOT_TOKEN").build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling()

if __name__ == '__main__':
    main()