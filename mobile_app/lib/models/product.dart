class Product {
  final String name;
  final String url;
  final String status;

  Product({
    required this.name,
    required this.url,
    required this.status,
  });

  factory Product.fromJson(Map<String, dynamic> json) {
    return Product(
      name: json['name'] ?? '',
      url: json['url'] ?? '',
      status: json['status'] ?? 'UNKNOWN',
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'name': name,
      'url': url,
      'status': status,
    };
  }
}
