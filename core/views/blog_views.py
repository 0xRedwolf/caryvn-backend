"""
Views and API endpoints for Caryvn Headless CMS Blog Engine.
Supports both public cached read-only APIs and authenticated Admin CRUD APIs.
"""
import logging
from django.db.models import F, Q
from django.utils.text import slugify
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from ..models import BlogPost, BlogAuthor, BlogCategory
from ..serializers import (
    BlogAuthorSerializer,
    BlogCategorySerializer,
    BlogPostListSerializer,
    BlogPostDetailSerializer,
)
from ..services.cloudinary_service import cloudinary_service

logger = logging.getLogger(__name__)


class BlogPagination(PageNumberPagination):
    page_size = 12
    page_size_query_param = 'page_size'
    max_page_size = 50


# =============================================================================
# PUBLIC BLOG API (No Auth Required — Fast & SEO Optimized)
# =============================================================================

class PublicBlogListView(APIView):
    """
    Public listing of published blog posts.
    Supports filtering by category, search term, and featured status.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        queryset = BlogPost.objects.filter(
            status=BlogPost.Status.PUBLISHED
        ).select_related('author', 'category').order_by('-published_at', '-created_at')

        # Category filter
        category_slug = request.query_params.get('category')
        if category_slug and category_slug != 'all':
            queryset = queryset.filter(category__slug=category_slug)

        # Search filter
        search_query = request.query_params.get('q')
        if search_query:
            queryset = queryset.filter(
                Q(title__icontains=search_query) |
                Q(excerpt__icontains=search_query) |
                Q(content__icontains=search_query)
            )

        # Optional featured-only filter
        if request.query_params.get('featured') == 'true':
            queryset = queryset.filter(featured=True)

        paginator = BlogPagination()
        page = paginator.paginate_queryset(queryset, request)
        if page is not None:
            serializer = BlogPostListSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = BlogPostListSerializer(queryset, many=True)
        return Response({'results': serializer.data})


class PublicBlogDetailView(APIView):
    """
    Public detail view of a single blog post by slug.
    Atomically increments views_count and returns related posts.
    """
    permission_classes = [AllowAny]

    def get(self, request, slug):
        try:
            post = BlogPost.objects.select_related('author', 'category').get(
                slug=slug,
                status=BlogPost.Status.PUBLISHED,
            )
        except BlogPost.DoesNotExist:
            return Response(
                {'error': 'Blog post not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Atomic view count increment
        BlogPost.objects.filter(pk=post.pk).update(views_count=F('views_count') + 1)
        post.refresh_from_db(fields=['views_count'])

        serializer = BlogPostDetailSerializer(post)
        data = serializer.data

        # Fetch 3 related posts in the same category
        related_qs = BlogPost.objects.filter(
            status=BlogPost.Status.PUBLISHED
        ).exclude(pk=post.pk)
        if post.category:
            related_qs = related_qs.filter(category=post.category)
        related_posts = related_qs.order_by('-published_at')[:3]
        data['related_posts'] = BlogPostListSerializer(related_posts, many=True).data

        return Response(data)


class PublicBlogCategoriesView(APIView):
    """Public list of blog categories."""
    permission_classes = [AllowAny]

    def get(self, request):
        categories = BlogCategory.objects.all().order_by('name')
        serializer = BlogCategorySerializer(categories, many=True)
        return Response(serializer.data)


class PublicBlogAuthorsView(APIView):
    """Public list of blog authors."""
    permission_classes = [AllowAny]

    def get(self, request):
        authors = BlogAuthor.objects.all().order_by('name')
        serializer = BlogAuthorSerializer(authors, many=True)
        return Response(serializer.data)


# =============================================================================
# ADMIN BLOG API (Requires IsAdminUser)
# =============================================================================

class AdminBlogPostListCreateView(APIView):
    """Admin endpoint to list all posts (including drafts) and create new posts."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        queryset = BlogPost.objects.select_related('author', 'category').order_by('-created_at')

        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        search_query = request.query_params.get('q')
        if search_query:
            queryset = queryset.filter(
                Q(title__icontains=search_query) |
                Q(slug__icontains=search_query)
            )

        paginator = BlogPagination()
        page = paginator.paginate_queryset(queryset, request)
        if page is not None:
            serializer = BlogPostDetailSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = BlogPostDetailSerializer(queryset, many=True)
        return Response({'results': serializer.data})

    def post(self, request):
        data = request.data.copy()

        # Auto-generate unique slug if not explicitly supplied
        if not data.get('slug'):
            base_slug = slugify(data.get('title', 'untitled'))
            slug = base_slug
            counter = 1
            while BlogPost.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            data['slug'] = slug

        serializer = BlogPostDetailSerializer(data=data)
        if serializer.is_valid():
            author_id = data.get('author_id')
            category_id = data.get('category_id')

            author = BlogAuthor.objects.filter(id=author_id).first() if author_id else None
            category = BlogCategory.objects.filter(id=category_id).first() if category_id else None

            post = serializer.save(author=author, category=category)
            return Response(BlogPostDetailSerializer(post).data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AdminBlogPostDetailView(APIView):
    """Admin endpoint to retrieve, update, and delete an individual post."""
    permission_classes = [IsAdminUser]

    def get_object(self, pk):
        try:
            return BlogPost.objects.select_related('author', 'category').get(pk=pk)
        except BlogPost.DoesNotExist:
            return None

    def get(self, request, pk):
        post = self.get_object(pk)
        if not post:
            return Response({'error': 'Post not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response(BlogPostDetailSerializer(post).data)

    def put(self, request, pk):
        post = self.get_object(pk)
        if not post:
            return Response({'error': 'Post not found'}, status=status.HTTP_404_NOT_FOUND)

        data = request.data.copy()
        serializer = BlogPostDetailSerializer(post, data=data, partial=True)
        if serializer.is_valid():
            author_id = data.get('author_id')
            category_id = data.get('category_id')

            update_kwargs = {}
            if 'author_id' in data:
                update_kwargs['author'] = BlogAuthor.objects.filter(id=author_id).first() if author_id else None
            if 'category_id' in data:
                update_kwargs['category'] = BlogCategory.objects.filter(id=category_id).first() if category_id else None

            updated_post = serializer.save(**update_kwargs)
            return Response(BlogPostDetailSerializer(updated_post).data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        post = self.get_object(pk)
        if not post:
            return Response({'error': 'Post not found'}, status=status.HTTP_404_NOT_FOUND)
        post.delete()
        return Response({'message': 'Post deleted successfully'}, status=status.HTTP_204_NO_CONTENT)


class AdminBlogImageUploadView(APIView):
    """
    Upload image to Cloudinary (or local media fallback) for featured banners or inline content.
    """
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        file_obj = request.FILES.get('image') or request.FILES.get('file')
        if not file_obj:
            return Response({'error': 'No image file provided'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            upload_result = cloudinary_service.upload_image(file_obj, folder='caryvn_blog')
            return Response({
                'url': upload_result['url'],
                'public_id': upload_result.get('public_id', ''),
                'is_cloud': upload_result.get('is_cloud', False),
            }, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"Image upload error: {e}")
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AdminBlogAuthorListCreateView(APIView):
    """Admin CRUD for Blog Authors."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        authors = BlogAuthor.objects.all().order_by('name')
        return Response(BlogAuthorSerializer(authors, many=True).data)

    def post(self, request):
        serializer = BlogAuthorSerializer(data=request.data)
        if serializer.is_valid():
            author = serializer.save()
            return Response(BlogAuthorSerializer(author).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AdminBlogAuthorDetailView(APIView):
    permission_classes = [IsAdminUser]

    def get_object(self, pk):
        return BlogAuthor.objects.filter(pk=pk).first()

    def get(self, request, pk):
        author = self.get_object(pk)
        if not author:
            return Response({'error': 'Author not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response(BlogAuthorSerializer(author).data)

    def put(self, request, pk):
        author = self.get_object(pk)
        if not author:
            return Response({'error': 'Author not found'}, status=status.HTTP_404_NOT_FOUND)
        serializer = BlogAuthorSerializer(author, data=request.data, partial=True)
        if serializer.is_valid():
            updated = serializer.save()
            return Response(BlogAuthorSerializer(updated).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        author = self.get_object(pk)
        if not author:
            return Response({'error': 'Author not found'}, status=status.HTTP_404_NOT_FOUND)
        author.delete()
        return Response({'message': 'Author deleted'}, status=status.HTTP_204_NO_CONTENT)


class AdminBlogCategoryListCreateView(APIView):
    """Admin CRUD for Blog Categories."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        categories = BlogCategory.objects.all().order_by('name')
        return Response(BlogCategorySerializer(categories, many=True).data)

    def post(self, request):
        data = request.data.copy()
        if not data.get('slug') and data.get('name'):
            data['slug'] = slugify(data['name'])
        serializer = BlogCategorySerializer(data=data)
        if serializer.is_valid():
            category = serializer.save()
            return Response(BlogCategorySerializer(category).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AdminBlogCategoryDetailView(APIView):
    permission_classes = [IsAdminUser]

    def get_object(self, pk):
        return BlogCategory.objects.filter(pk=pk).first()

    def get(self, request, pk):
        category = self.get_object(pk)
        if not category:
            return Response({'error': 'Category not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response(BlogCategorySerializer(category).data)

    def put(self, request, pk):
        category = self.get_object(pk)
        if not category:
            return Response({'error': 'Category not found'}, status=status.HTTP_404_NOT_FOUND)
        data = request.data.copy()
        if not data.get('slug') and data.get('name'):
            data['slug'] = slugify(data['name'])
        serializer = BlogCategorySerializer(category, data=data, partial=True)
        if serializer.is_valid():
            updated = serializer.save()
            return Response(BlogCategorySerializer(updated).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        category = self.get_object(pk)
        if not category:
            return Response({'error': 'Category not found'}, status=status.HTTP_404_NOT_FOUND)
        category.delete()
        return Response({'message': 'Category deleted'}, status=status.HTTP_204_NO_CONTENT)
