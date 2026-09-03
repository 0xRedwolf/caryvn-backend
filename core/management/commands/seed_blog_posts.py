"""
Management command to seed initial blog categories, author, and all 12 existing articles
into the database from frontend/src/app/blog/[slug]/page.tsx.

Usage:
    python manage.py seed_blog_posts
"""
import re
from pathlib import Path
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import BlogAuthor, BlogCategory, BlogPost


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

        # Post metadata and categorizations
        posts_metadata = {
            'what-is-an-smm-panel': {
                'category': 'Guides',
                'featured_image': '/cat-guides.png',
            },
            'best-smm-tools-2026': {
                'category': 'Tools',
                'featured_image': '/cat-tools.png',
            },
            'best-platform-for-business': {
                'category': 'Strategy',
                'featured_image': '/cat-strategy.png',
            },
            'increase-engagement-2026': {
                'category': 'Strategy',
                'featured_image': '/cat-strategy.png',
            },
            'social-media-trends-2026': {
                'category': 'Trends',
                'featured_image': '/cat-trends.png',
            },
            'how-often-to-post-2026': {
                'category': 'Strategy',
                'featured_image': '/cat-strategy.png',
            },
            'beat-social-media-algorithm-2026': {
                'category': 'Strategy',
                'featured_image': '/cat-strategy.png',
            },
            'organic-vs-paid-social-2026': {
                'category': 'Strategy',
                'featured_image': '/cat-strategy.png',
            },
            'tiktok-algorithm-2026': {
                'category': 'TikTok',
                'featured_image': '/cat-tiktok.png',
            },
            'social-proof-ecommerce': {
                'category': 'Strategy',
                'featured_image': '/cat-strategy.png',
            },
            'instagram-vs-youtube-roi': {
                'category': 'Strategy',
                'featured_image': '/cat-strategy.png',
            },
            'top-10-best-smm-panels-2026': {
                'category': 'Tools',
                'featured': True,
                'featured_image': '/blog-hero.png',
            },
        }

        # 3. Read content directly from frontend/src/app/blog/[slug]/page.tsx
        frontend_page = Path(__file__).resolve().parents[4] / 'frontend' / 'src' / 'app' / 'blog' / '[slug]' / 'page.tsx'

        if not frontend_page.exists():
            self.stdout.write(self.style.ERROR(f"Could not locate {frontend_page}"))
            return

        file_text = frontend_page.read_text(encoding='utf-8')

        seeded_count = 0
        for slug, meta in posts_metadata.items():
            # Look for block: 'slug': { ... }
            pattern = re.compile(rf"['\"]?{re.escape(slug)}['\"]?\s*:\s*\{{", re.MULTILINE)
            match = pattern.search(file_text)
            if not match:
                self.stdout.write(self.style.WARNING(f"Could not find block for slug: {slug}"))
                continue

            start_idx = match.start()
            # Simple extractor of title, seoTitle, seoDescription, readTime, content
            sub_text = file_text[start_idx:start_idx + 12000]

            title_m = re.search(r"title:\s*['\"](.*?)['\"],", sub_text)
            title = title_m.group(1) if title_m else slug.replace('-', ' ').title()

            seo_title_m = re.search(r"seoTitle:\s*['\"](.*?)['\"],", sub_text)
            seo_title = seo_title_m.group(1) if seo_title_m else title

            seo_desc_m = re.search(r"seoDescription:\s*['\"](.*?)['\"],", sub_text)
            seo_desc = seo_desc_m.group(1) if seo_desc_m else ''

            read_time_m = re.search(r"readTime:\s*['\"](.*?)['\"],", sub_text)
            read_time = read_time_m.group(1) if read_time_m else '6 min read'

            # Extract content between content: ` and `
            content_m = re.search(r"content:\s*`([\s\S]*?)`", sub_text)
            content = content_m.group(1).strip() if content_m else f"<p>{seo_desc}</p>"

            # Extract FAQs if present
            faqs = []
            faqs_block = re.search(r"faqs:\s*\[([\s\S]*?)\]\s*,", sub_text)
            if faqs_block:
                faq_items = re.findall(r"\{\s*q:\s*['\"](.*?)['\"],\s*a:\s*['\"](.*?)['\"]\s*\}", faqs_block.group(1), re.DOTALL)
                for q, a in faq_items:
                    faqs.append({'q': q.strip(), 'a': a.strip()})

            cat_name = meta.get('category', 'Strategy')
            cat_obj = db_categories.get(cat_name)

            post, created_post = BlogPost.objects.update_or_create(
                slug=slug,
                defaults={
                    'title': title,
                    'seo_title': seo_title,
                    'seo_description': seo_desc,
                    'excerpt': seo_desc,
                    'content': content,
                    'author': author,
                    'category': cat_obj,
                    'status': BlogPost.Status.PUBLISHED,
                    'featured': meta.get('featured', False),
                    'read_time': read_time,
                    'featured_image': meta.get('featured_image', '/cat-strategy.png'),
                    'faqs': faqs,
                    'published_at': timezone.now(),
                }
            )

            status_str = "Created" if created_post else "Updated"
            self.stdout.write(self.style.SUCCESS(f"[{status_str}] {post.title} (slug: {slug})"))
            seeded_count += 1

        self.stdout.write(self.style.SUCCESS(f"\nSuccessfully seeded {seeded_count} dynamic blog articles!"))
