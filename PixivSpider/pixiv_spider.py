# -*- coding:utf-8 -*-
import sys
from collections import deque
from http import cookiejar
from math import ceil
from queue import Queue
import json
import logging
from datetime import datetime, timedelta

import requests
from requests.exceptions import ConnectionError
from lxml import etree

from PixivSpider.setting import *
from PixivSpider.base import *

__all__ = ['Pixiv', 'PixivDownload', 'PixivPainterInfo', 'PixivPictureInfo', 'PixivAllPictureOfPainter',
           'PixivOperatePicture', 'PixivBookmark']

logging.basicConfig(level=logging.DEBUG)


class Pixiv(requests.Session):  # Just achieve login function
    def __init__(self):
        super(Pixiv, self).__init__()
        self.__form_data = form_data
        self.headers.update({'User-Agent': User_Agent})
        self.cookies = cookiejar.LWPCookieJar(filename=COOKIE_FILE)

    def _get_postkey(self):
        login_content = self.get(url_tuple.login_url)
        try:
            post_key = re_tuple.post_key.findall(login_content.text)[0]
        except IndexError:
            print("Don't get post_key...")
            raise
        else:
            return post_key

    @staticmethod
    def _get_my_id(cookies_file=COOKIE_FILE):
        cookies = cookiejar.LWPCookieJar()
        cookies.load(cookies_file, ignore_discard=True)
        cookies_dict = requests.utils.dict_from_cookiejar(cookies)  # 加载cookies文件，转为字典
        account_id = cookies_dict['PHPSESSID'].split('_')[0]  # 提取account id
        return account_id

    def login_with_cookies(self):
        try:
            self.cookies.load(filename=COOKIE_FILE, ignore_discard=True)  # cookie过期就会登录失败
        except FileNotFoundError:
            return False
        else:
            # return True  # don't check out login status.
            # 因为太耗资源暂时关闭这个已登陆的检测...
            # if self.already_login():
            #     return True
            # return False
            return True

    def login_with_account(self, pixiv_id=None, pixiv_passwd=None):
        if pixiv_id is not None and pixiv_passwd is not None:  # 保证帐号和密码不能全为空
            self.__form_data['pixiv_id'] = pixiv_id
            self.__form_data['password'] = pixiv_passwd
            self.__form_data['post_key'] = self._get_postkey()
            result = self.post(url_tuple.post_url, data=self.__form_data)
            # print(result.text)
            # print(result.status_code)
            if result.status_code == 200 and self.already_login():
                # 只要网络连接没有问题，http status code 应该就是200
                # 通过访问个人信息页面判定，用户是否真正登录，并且访问页面之后，存的cookies比不访问页面要多字段
                # 可以拿到真正的用户ID（PHPSESSID）（个人信息页面的Pixiv id 为假）
                self.cookies.save(ignore_discard=True)  # 保存cookies
                return True
        return False

    def login(self, pixiv_id=None, pixiv_passwd=None):
        if self.login_with_cookies():
            return True
        else:
            return self.login_with_account(pixiv_id, pixiv_passwd)

    def already_login(self):
        resp = self.get(url_tuple.setting_url, allow_redirects=False)  # 禁止重定向
        return resp.status_code == 200


class PixivDownload(Pixiv):  # pure download a item
    def __init__(self, picture_id):
        super(PixivDownload, self).__init__()
        self.picture_id = picture_id
        self.resp = None
        self.__picture_base_info = tuple()

    def get_detail_page_resp(self):
        resp = self.get(picture_detail_page_mode.format(picture_id=self.picture_id))
        print(picture_detail_page_mode.format(picture_id=self.picture_id))
        print(resp.status_code)
        return resp

    def _get_img_data(self, picture_id=None, date=None, p=None, file_type=None, img_url=None):
        headers = self.headers
        headers['Host'] = 'www.pixiv.net'  # modify the most important headers info
        headers['Referer'] = picture_detail_page_mode.format(picture_id=picture_id)
        if img_url is None:  # Not yet realized.
            if picture_id is not None and date is not None and p is not None and file_type is not None:
                # I think it should have a better way to achieve this function
                # instead of using four 'not None' judgments.
                img_url = self._get_real_url(picture_id, date, p, file_type)
            else:
                print('{}参数输入错误,无法构造url...'.format(self._get_img_data.__name__))
                sys.exit(1)
        img_data = self.get(img_url, headers=headers)  # not get binary data of picture, get resp object
        if img_data.status_code == 200:
            print('图片源页面访问成功...{}'.format(self.picture_id))
            return img_data.content  # return binary data of picture
        elif img_data.status_code == 403:
            print('被禁止，请检查是否为headers设置出错...{}'.format(self.picture_id))
        else:
            print('访问源页面出错，错误代码为:{}...{}'.format(img_data.status_code, self.picture_id))
        return None

    def download_picture(self, p=None, dirname=save_folder):
        save_path_list = None
        if self.resp is None:
            self.resp = self.get_detail_page_resp()
        selector = etree.HTML(self.resp.text)
        try:  # 无论用户指不指定p，我们都应该检测是否为多P，多P作品的p0与单作的p0爬取方式不同
            page_count = selector.xpath('//div[@class="page-count"]/span/text()')[0]
            print(page_count)
        except IndexError:
            print('这不是一个多P图片...{}'.format(self.picture_id))
            save_path_list = self._get_one_picture(picture_id=self.picture_id, selector=selector, dirname=dirname)
        else:  # 有时候真的不喜欢OOP, 更喜欢这样实际传参.
            print('这是一个多P图片...')
            save_path_list = self._get_picture_part(self.picture_id, p_max=int(page_count), p_num=p, dirname=dirname)
        finally:
            return save_path_list

    def download_picture_directly(self, dirname=save_folder, **kwargs):
        """
        Download pictures via key information.

        :param dirname: The directory that saves pictures.
        :param kwargs: Four keyword parameters about picture basic information.
        :return: The file path of the downloaded picture.
        """
        try:
            picture_id, p, date, file_type = kwargs['picture_Id'], kwargs['p'], kwargs['date'], kwargs['file_type']
        except KeyError:
            print('提交的参数有错误...{}'.format(kwargs))
            raise
        try:
            resp = self._get_img_data(picture_id=picture_id, p=p, date=date, file_type=file_type)
        except Exception as e:
            print('下载失败...{}'.format(picture_id))
            return None
        else:
            save_path = self._save_img_file(
                filename=self._get_complete_filename(picture_id, p, file_type),
                img_data=resp,
                dirname=dirname
            )
            print('下载成功...{}'.format(picture_id))
            return [save_path]

    def _get_picture_part(self, picture_id, p_num=None, p_max=None, dirname=None):
        """
        get the binary data of picture which not only has one page.

        :param picture_id: the unique id of picture.
        :param p_num: the specific page of the picture.
        :param p_max: the number of page of the picture.
                      Note: p_num or p_max must is not None.
        :param dirname: the directory of saving the picture.
        :return: [the path name of picture, ...]
        """
        save_path_list = []
        operate_list = []
        if p_num is not None:
            operate_list.append(p_num)
        elif p_max is not None:
            operate_list.extend(range(p_max))
        print(operate_list)

        for p in operate_list:
            picture_binary_data, info_tuple = self._get_one_picture_part(picture_id, p)
            _, p, date, file_type = info_tuple
            if picture_binary_data is not None:
                save_path = self._save_img_file(filename=self._get_complete_filename(picture_id, p, file_type),
                                                img_data=picture_binary_data,
                                                dirname=dirname)
                save_path_list.append(save_path)
        return save_path_list

    def _get_one_picture(self, picture_id, selector, dirname):
        try:
            original_image = selector.xpath('//img[@class="original-image"]/@data-src')[0]
        except IndexError:
            raise
        else:
            resp = self._get_img_data(picture_id=self.picture_id, img_url=original_image)
            if resp is not None:
                self.__picture_base_info = list(self.split_info(original_image))
                # add painter_id to info list, just for compatibility and interface.
                self.__picture_base_info.insert(1, None)
                picture_id, p, date, file_type = self.split_info(original_image)

                save_path = self._save_img_file(filename=self._get_complete_filename(picture_id, p, file_type),
                                                img_data=resp,
                                                dirname=dirname)
                print('下载成功...{}'.format(picture_id))
                return [save_path]
            else:
                print('下载失败...{}'.format(self.picture_id))
                return None

    def _get_one_picture_part(self, picture_id, p_num):
        picture_part_url = picture_part_detail_page_mode.format(picture_id=picture_id, page=p_num)
        resp_text = self.get(picture_part_url).text
        real_url = self._get_real_url_from_part_page(resp_text)
        info_tuple = self.split_info(real_url)
        picture_binary_data = self._get_img_data(img_url=real_url)
        return picture_binary_data, info_tuple

    @staticmethod
    def _get_real_url_from_part_page(resp_text):
        selector = etree.HTML(resp_text)
        real_url = selector.xpath('//body/img/@src')
        return real_url[0]

    @staticmethod
    def split_info(url):
        picture_id = re_tuple.picture_id.findall(url)[0]
        p = re_tuple.p_from_source.findall(url)[0]
        date = re_tuple.date.findall(url)[0]
        file_type = url.split('.')[-1]
        print(picture_id, p, date, file_type)
        return picture_id, p, date, file_type  # return four elements tuple

    @staticmethod
    def _get_real_url(picture_id, date, p, file_type):
        work_img_url = after_str_mode.format(date=date, picture_id=picture_id, p=p, file_type=file_type)
        return work_img_url

    @staticmethod
    def _get_complete_filename(picture_id, p, file_type):
        return str(picture_id) + '_p' + str(p) + '.' + str(file_type)

    @staticmethod
    def _save_img_file(filename, img_data, dirname):
        file_path = os.path.join(dirname, filename)  # 这个地方可能因为用户乱定义，造成错误
        if not os.path.exists(file_path):
            with open(file=file_path, mode='wb') as f:  # 可能报错,如果目录不存在
                f.write(img_data)
                print('{}保存成功...'.format(filename))
                return file_path  # 将保存路径返回，用做gui显示图片
                # log -> log.txt
        else:
            print('{}文件已经存在....'.format(filename))
            return file_path  # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!临时解决方案！！！！！！！！删去

    # @staticmethod  # temporarily stop using.
    # def operate_dir(painter_id):  # get a specific artist's work path.
    #     specific_path = os.path.join(save_folder, str(painter_id))
    #     if os.path.exists(specific_path):
    #         pass
    #     else:
    #         try:
    #             os.makedirs(specific_path)
    #         except Exception:
    #             raise
    #     return specific_path

    def get_resp_text(self):  # return picture page html text
        try:
            return self.resp.text
        except AttributeError as e:
            print(e)
            print('使用了download_picture_directly函数，没有resp')
            return None

    @property
    def picture_base_info(self):
        return self.__picture_base_info


class PixivPictureInfo(Pixiv):  # deal with specific picture information
    def __init__(self, picture_id=None):
        super(PixivPictureInfo, self).__init__()
        self.picture_id = picture_id

    def get_picture_info(self, resp=None):
        info_list = []
        if resp is None:
            r = self.get(picture_detail_page_mode.format(picture_id=self.picture_id))
            if r.status_code == 200:
                resp = r.text
            else:
                print('访问图片具体页面失败:{}'.format(self.picture_id))
                return info_list  # 如果访问失败，则返回一个空列表
        info_list = self._parse_picture_html(resp)
        info_list.insert(0, self.picture_id)
        info_list.append(None)  # already add to bookMark.
        return info_list

    def _parse_picture_html(self, html_text):
        data_list = []
        selector = etree.HTML(html_text)
        try:
            section = selector.xpath('//section[@class="work-info"]')[0]
        except IndexError:
            print('Get work_info section failure.')
            raise
        else:
            data_list.append(self._parse_work_title(section))
            data_list.append(self._parse_work_introduction(section))
        return data_list

    @staticmethod
    def _parse_work_title(section):
        title = section.xpath('h1[@class="title"]/text()')[0]
        return title

    @staticmethod
    def _parse_work_introduction(section):
        introduction = None
        try:
            introduction = section.xpath('//p[@class="caption"]')[0].xpath('string(.)').strip()
        except IndexError:
            pass
        finally:
            return introduction


class PixivOperatePicture(Pixiv):
    def __init__(self, picture_id=None):
        super(PixivOperatePicture, self).__init__()
        self.picture_id = picture_id
        self.__bookmark_form_data = bookmark_add_form_data

    def bookmark_add(self, comment='', tag=''):  # 总感觉标签这里会出现玄学错误
        headers = self.headers  # 重复提交会修改备注与标签
        headers.update({'Host': 'www.pixiv.net', 'Origin': 'https://www.pixiv.net',
                        'Referer': 'https://www.pixiv.net/bookmark_add.php?type=illust&illust_id={}'.format(
                            self.picture_id)})  # 实际上改不改headers一点也不影响程序，现阶段。
        self.__bookmark_form_data['id'] = self.picture_id
        self.__bookmark_form_data['comment'] = comment  # 备注
        self.__bookmark_form_data['tag'] = tag  # 标签，空格分割

        r = self.post('https://www.pixiv.net/bookmark_add.php?id={}'.format(self.picture_id), headers=headers,
                      data=self.__bookmark_form_data)
        if r.status_code != 200:
            self.operate_tt()
            r = self.post('https://www.pixiv.net/bookmark_add.php?id={}'.format(self.picture_id), headers=headers,
                          data=self.__bookmark_form_data)
        return r.status_code == 200
        # 点了按钮，貌似可以得到喜欢数，不过貌似必要不大。

    def operate_tt(self):  # 通过访问主页更新tt的值
        r = self.get(main_page)
        selector = etree.HTML(r.text)
        tt = selector.xpath('//input[@name="tt"]/@value')[0]
        with open(token_path, 'wt') as f:  # 修改token文件中的tt
            f.write(tt)
        self.__bookmark_form_data['tt'] = tt  # 修改setting和实例属性中的tt
        bookmark_add_form_data['tt'] = tt

    def like_add(self):
        pass


class PixivPainterInfo(Pixiv):  # get painter's personal information.
    def __init__(self, painter_id=None, picture_id=None):
        super(PixivPainterInfo, self).__init__()
        self.painter_id = painter_id
        self.picture_id = picture_id

    # Abandoned, we shouldn't premature optimization!!!
    def get_painter_id_from_work_detail_page(self, resp=None):
        if resp is None and self.picture_id:
            resp = self.get(picture_detail_page_mode.format(picture_id=self.picture_id)).text
        selector = etree.HTML(resp)
        # painter_name = selector.xpath('//a[@class="user-name"]/@title')[0]
        painter_id = selector.xpath('//a[@class="user-name"]/@href')[0].split('=')[-1]
        self.painter_id = painter_id
        return painter_id
        # return self.get_painter_id_info()  # get painter detail info.

    def get_painter_info(self):  # main function (DEFAULT: Get information from personal pages)
        r = self.get(personal_info_mode.format(painter_id=self.painter_id))
        info_dict = self._parse_html(r.text)
        info_dict['ID'] = self.painter_id
        return info_dict

    def _parse_html(self, html_text):  # 用于分析所有table的，现阶段只有profile这个table
        data_dict = {}
        selector = etree.HTML(html_text)
        try:
            info_table = selector.xpath('//table[@class="ws_table profile"]')[0]
        except IndexError:
            print('Get info_table failure.')
            raise
        else:
            data_dict['Profile'] = self._parse_profile(info_table)
        return data_dict

    @staticmethod
    def _parse_profile(info_table):
        selector = info_table.xpath('tr')
        info_dict = {}
        for tr in selector:  # 使用xpath的全局 '//' 貌似仍会匹配到全局
            td1_text = tr.xpath('td[@class="td1"]/text()')[0]
            td2_text = tr.xpath('td[@class="td2"]')[0].xpath('string(.)').strip()
            info_dict[td1_text] = td2_text
        return info_dict

    def save_to_db(self):
        pass


class PixivAllPictureOfPainter(Pixiv):  # Get all the pictures of a specific artist.
    def __init__(self, painter_id=None):
        super(PixivAllPictureOfPainter, self).__init__()
        self.picture_num = None
        self.picture_deque = deque()
        self.painter_id = painter_id
        # self.already_download_picture = find_all_picture_id(conn)
        self.already_download_picture = []
        self.picture_num = 0
        self.page_num = 0
        self.main_page = list_of_works_mode.format(painter_id=painter_id)
        # self.painter_dirname = self.operate_dir(painter_id)
        # self.painter_dir_exist = False

    def _get_work_info(self):  # Add data tuple to the work queue.
        if self.page_num >= 1:
            resp_text = self.get(self.main_page).text
            temp_data_list = self._get_each_work_info(etree.HTML(resp_text))
            for data in temp_data_list:
                self.picture_deque.append(data)
        if self.page_num >= 2:
            base_url = list_of_works_mode.format(painter_id=self.painter_id)
            for each_page in range(2, self.page_num + 1):
                try:
                    # works_params = {'type': 'all', 'p': each_page}
                    result = self.get(base_url + '&type=all&p={}'.format(each_page))
                except Exception:
                    raise
                else:
                    temp_data_list = self._get_each_work_info(etree.HTML(result.text))
                    for data in temp_data_list:
                        self.picture_deque.append(data)  # only have picture id

    def _get_each_work_info(self, selector):  # return a work data of picture.
        original_img_url = selector.xpath('//img[@data-src]/@data-src')
        temp_data_list = []
        for item in original_img_url:
            id_str = re_tuple.picture_id.findall(item)[0]
            # p_str = re_tuple.p.findall(item)[0]
            # date_str = re_tuple.date.findall(item)[0]
            if int(id_str) not in self.already_download_picture:
                # return id_str, date_str, p_str
                temp_data_list.append(id_str)
            else:
                print('图片{}已经存在，不再加入下载队列中...'.format(id_str))  # 图片并不是本地存在而是存在于数据库中，这里为歧义。
        return temp_data_list

    def get_work_of_painter(self):  # 异步之后，这个函数八成废掉
        get_page_num(self)
        self._get_work_info()
        print(self.picture_deque)
        for picture_id in self.picture_deque:
            temp = PixivDownload(picture_id)  # 这种实现方式，真的有毒。。。。
            temp.login()
            temp.download_picture()

            # img_url = picture_detail_page_mode.format(picture_id)
            # self.download_picture(img_url=img_url)


class PixivRank(object):
    """
    Get the rank of pixiv.net.
    """
    def __init__(self):
        super(PixivRank, self).__init__()

    def get_daily_rank(self, date, p=1):
        """
        Get daily ranking data of Pixiv.net.
        :param date: An object that represent time, such as str or int or datetime.datetime object.
        :param p: p represents the part of daily work.
        :return: json data that contain information.
        """
        json_data = None
        url = self.get_daily_rank_url(date, p)
        try:
            r = requests.get(url)
        except ConnectionError as e:
            logging.error(e)
            # raise
        else:
            json_data = json.loads(r.text)
        return json_data

    def get_daily_rank_url(self, date, p):
        if isinstance(date, datetime):
            date = self.convert_date_format(date)
        elif isinstance(date, int) or isinstance(date, str):
            pass
        else:
            logging.error('Func parameter error.')
            return
        url = daily_rank_info_mode.format(date=date, p=p)
        logging.debug('Daily_rank_url: {}'.format(url))
        return url

    @staticmethod
    def convert_date_format(date):
        """
        Convert a datetime.datetime object to a string.
        eg: 2018-05-04 21:11:46.316617 -> 20180504
        :param date: a datetime.datetime object
        :return: str
        """
        return str(date.year) + str(date.month).zfill(2) + str(date.day).zfill(2)


def get_page_num(cls):
    resp = cls.get(getattr(cls, 'main_page'))
    selector = etree.HTML(resp.text)
    try:
        picture_num_text = selector.xpath('//span[@class="count-badge"]/text()')[0]
    except IndexError:
        print('Get picture_num failure.')
        raise
    else:
        picture_num = int(re_tuple.num.findall(picture_num_text)[0])
        page_num = int(ceil(picture_num / picture_num_of_each_page))
        print(picture_num, page_num)
        setattr(cls, 'page_num', page_num)  # 这样动态添加属性真的好吗？？？
        setattr(cls, 'picture_num', picture_num)


# class PixivDownloadAlone(PixivDownload, PixivPainterInfo, PixivPictureInfo):  # Give the work ID and get the artist ID
#     def __init__(self, picture_id):
#         super(PixivDownloadAlone, self).__init__(picture_id=picture_id)
#         self.__form_data = bookmark_add_form_data
#         # self.resp = self.get_detail_page_resp()  # 加入数据库之后，这一项将不是必要的, 会在登录之前初始化，GG
#         self.resp = None
#
#     def get_pid_from_work(self):
#         if self.resp is None:
#             self.resp = self.get_detail_page_resp()
#         selector = etree.HTML(self.resp.text)
#         try:
#             pid = selector.xpath('//a[@class="user-name"]/@href')[0].split('=')[-1]  # picture_id -> artist_id
#         except IndexError:
#             raise
#         else:
#             self.painter_id = pid
#             return pid


class PixivBookmark(Pixiv):
    def __init__(self, painter_id=None):
        super(PixivBookmark, self).__init__()
        self.painter_id = painter_id if painter_id else self._get_my_id()  # 默认为自己的ID
        self.main_page = 'https://www.pixiv.net/bookmark.php?id={}&rest=show'.format(self.painter_id)
        self.picture_num = 0
        self.page_num = 0
        self.picture_deque = deque()  # 存储所有书签信息

    def get_html(self):  # a[class="bookmark-count _ui-tooltip"]  # ???喵喵喵???
        r = self.get(self.main_page)  # 要不要禁止重定向

    def get_bookmark_info(self):  # 其实 p=1 这个参数可以传，不像作品主页一样会报错，所以这里可以简化代码
        get_page_num(self)  # 动态增加属性: 1. self.page_num 2. self.picture_num
        if self.page_num >= 1:
            resp_text = self.get(self.main_page).text
            selector = etree.HTML(resp_text).xpath('//ul[@class="_image-items js-legacy-mark-unmark-list"]')[0]
            temp_data_list = self._get_each_bookmark_info(selector)
            self.picture_deque.extend(temp_data_list)
        sign = 1
        if self.page_num >= 2:
            for p in range(2, self.page_num + 1):
                sign += 1
                print('-------------------第{}页-----------------------'.format(sign))
                try:
                    resp_text = self.get(self.main_page + '&p={}'.format(p)).text
                except Exception:
                    raise
                else:
                    selector = etree.HTML(resp_text).xpath('//ul[@class="_image-items js-legacy-mark-unmark-list"]')[0]
                    temp_data_list = self._get_each_bookmark_info(selector)
                    self.picture_deque.extend(temp_data_list)
        return self.picture_deque  # 将全部数据返回

    @staticmethod
    def _get_each_bookmark_info(selector):
        all_li = selector.xpath('li[@class="image-item"]')
        temp_data_list = []
        for li in all_li:
            try:
                title = li.xpath('a/h1[@class="title"]/text()')[0]
            except IndexError:
                title = li.xpath('h1[@class="title"]/text()')[0]  # 奇葩：有的错误页面竟然是这个结构
            if title != '-----':  # 非公开或者删除，貌似有几率误伤？？如果把作品名起成 ------
                base_selector = li.xpath('a/div[@class="_layout-thumbnail"]')[0]
                tags = base_selector.xpath('img/@data-tags')[0]
                picture_id = base_selector.xpath('img/@data-id')[0]
                painter_id = li.xpath('a[@class="user ui-profile-popup"]/@data-user_id')[0]
                painter_name = li.xpath('a[@class="user ui-profile-popup"]/@data-user_name')[0]
                mark_num = li.xpath('ul[@class="count-list"]/li/a[@class="bookmark-count _ui-tooltip"]/text()')[0]
                temp_data_list.append((title, tags, picture_id, painter_id, painter_name, mark_num))
            else:
                print('非公开或者删除(貌似是这样...)')
                # try:  # 其实这里没必要分开。。。。。。而且很明显，分类不只这3中，还有很多看不懂的。GG
                #     base_selector = li.xpath('a[@class="work  _work "]/div[@class="_layout-thumbnail"]')[0]  # 普通图片
                # except IndexError:
                #     try:
                #         base_selector = li.xpath('a[@class="work  _work multiple "]'
                #                                  '/div[@class="_layout-thumbnail"]')[0]  # 多图片
                #     except IndexError:
                #         base_selector = li.xpath('a[@class="work  _work ugoku-illust "]'
                #                                  '/div[@class="_layout-thumbnail"]')[0]  # 动态图片
        return temp_data_list  # 这里可以写入 .xlsx 文件，以便后期分析使用


class PixivBase(Pixiv):
    def __init__(self):
        super(PixivBase, self).__init__()


if __name__ == "__main__":
    # x = PixivDownload(picture_id=55739666)
    # x.login()
    # x.download_picture()
    x = PixivRank()
    x.get_daily_rank('20180101')
# sometimes naive.
