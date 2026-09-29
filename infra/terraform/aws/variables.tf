variable "region" {
  type    = string
  default = "ap-south-1"
}
variable "env" {
  type    = string
  default = "demo"
}
variable "cluster_version" {
  type    = string
  default = "1.30"
}
variable "db_instance_class" {
  type    = string
  default = "db.r6g.large"
}
