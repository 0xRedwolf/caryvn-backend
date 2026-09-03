"""
Management command to seed initial blog categories, author, and all 12 existing articles
into the database using self-contained seed_data.py.

Usage:
    python manage.py seed_blog_posts
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import BlogAuthor, BlogCategory, BlogPost
from .seed_data import BLOG_POSTS_DATA


class Command(BaseCommand):
    help = 'Seeds database with initial blog author, categories, and all 12 existing blog articles'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE('Starting blog database seed...'))

        # 1. Create or get default Author
        author, created = BlogAuthor.objects.get_or_create(
            name='Alexander Sterling',
            defaults={
                'role': 'Editor In Chief',
                'bio': 'Alexander is the editor-in-chief of the Caryvn blog, specializing in social media growth, digital brand building, and algorithmic engagement strategy. With years of experience optimizing social panels, Alexander shares actionable insights for scaling online visibility.',
                'social_x': 'https://x.com/caryvn_official',
                'social_linkedin': 'https://linkedin.com/company/caryvn',
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'Created default author: {author.name}'))
        else:
            self.stdout.write(f'Using existing author: {author.name}')

        # 2. Create standard Categories
        category_map = {
            'Tools': ('tools', 'Evaluations, comparisons, and feature breakdowns of top SMM software.'),
            'Strategy': ('strategy', 'Tactical blueprints, hook frameworks, and engagement growth playbooks.'),
            'Trends': ('trends', 'Industry shifts, consumer behavior updates, and digital marketing news.'),
            'Guides': ('guides', 'Step-by-step masterclasses and instructional walkthroughs.'),
            'TikTok': ('tiktok-growth', 'Specialized guides for mastering TikTok algorithms and FYP placement.'),
        }

        db_categories = {}
        for cat_name, (cat_slug, cat_desc) in category_map.items():
            cat_obj, _ = BlogCategory.objects.get_or_create(
                slug=cat_slug,
                defaults={'name': cat_name, 'description': cat_desc}
            )
            db_categories[cat_name] = cat_obj

        # 3. Seed all 12 articles from BLOG_POSTS_DATA
        seeded_count = 0
        for slug, item in BLOG_POSTS_DATA.items():
            cat_name = item.get('category', 'Strategy')
            cat_obj = db_categories.get(cat_name)

            post, created_post = BlogPost.objects.update_or_create(
                slug=slug,
                defaults={
                    'title': item['title'],
                    'seo_title': item.get('seo_title', item['title']),
                    'seo_description': item.get('seo_description', ''),
                    'excerpt': item.get('seo_description', ''),
                    'content': item['content'],
                    'author': author,
                    'category': cat_obj,
                    'status': BlogPost.Status.PUBLISHED,
                    'featured': item.get('featured', False),
                    'read_time': item.get('read_time', '7 min read'),
                    'featured_image': item.get('featured_image', '/cat-strategy.png'),
                    'faqs': item.get('faqs', []),
                    'published_at': timezone.now(),
                }
            )

            status_str = "Created" if created_post else "Updated"
            self.stdout.write(self.style.SUCCESS(f"[{status_str}] {post.title} (slug: {slug})"))
            seeded_count += 1

        self.stdout.write(self.style.SUCCESS(f"\nSuccessfully seeded {seeded_count} dynamic blog articles!"))
